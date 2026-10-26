"""Read-only corpus, manifest, truncation and active-index audit."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models import Document
from app.services.retrieval.hybrid_store import HybridStore
from app.services.retrieval.milvus_store import MilvusStore
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_tokenizer():
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(
            "BAAI/bge-large-zh-v1.5",
            cache_dir=str(ROOT / "storage" / "models"),
            local_files_only=True,
            trust_remote_code=False,
        )
    except (OSError, RuntimeError, ValueError):
        return None


def manifest_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


async def collect(folder: Path | None, knowledge_base_id: UUID, check_index: bool):
    async with async_session_factory() as session:
        documents = list(
            (
                await session.scalars(
                    select(Document)
                    .where(Document.knowledge_base_id == knowledge_base_id)
                    .order_by(Document.created_at)
                )
            ).all()
        )

    by_hash = defaultdict(list)
    status_counts = Counter()
    for document in documents:
        by_hash[document.sha256].append(document)
        status_counts[document.status] += 1

    manifest_stats = {
        "documents_with_active_manifest": 0,
        "missing_manifest_files": [],
        "invalid_manifests": [],
        "empty_documents": [],
        "chunk_count": 0,
        "nonempty_chunks": 0,
        "chunks_needing_review": 0,
        "markdown_documents_with_heading_path": 0,
        "markdown_documents_without_heading_path": [],
        "models": Counter(),
        "collections": Counter(),
        "chunkers": Counter(),
        "oversize_chunks": [],
    }
    tokenizer = load_tokenizer() if check_index else None
    active = [document for document in documents if document.active_manifest]
    for document in active:
        manifest_stats["documents_with_active_manifest"] += 1
        path = manifest_path(document.active_manifest)
        if path is None or not path.is_file():
            manifest_stats["missing_manifest_files"].append(
                {"document_id": str(document.id), "manifest": document.active_manifest}
            )
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            chunks = manifest["chunks"]
            if len(chunks) != manifest["chunk_count"]:
                raise ValueError("chunk count mismatch")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            manifest_stats["invalid_manifests"].append(
                {"document_id": str(document.id), "error": type(exc).__name__}
            )
            continue
        manifest_stats["chunk_count"] += len(chunks)
        manifest_stats["models"][manifest.get("model_name")] += 1
        manifest_stats["collections"][manifest.get("collection_name") or "legacy"] += 1
        manifest_stats["chunkers"][manifest.get("chunker_version")] += 1
        nonempty = [chunk for chunk in chunks if chunk.get("text", "").strip()]
        manifest_stats["nonempty_chunks"] += len(nonempty)
        manifest_stats["chunks_needing_review"] += sum(bool(c.get("needs_review")) for c in chunks)
        if not nonempty:
            manifest_stats["empty_documents"].append(str(document.id))

        if document.original_filename.lower().endswith(".md"):
            if any(chunk.get("heading_path") for chunk in chunks):
                manifest_stats["markdown_documents_with_heading_path"] += 1
            else:
                manifest_stats["markdown_documents_without_heading_path"].append(
                    str(document.id)
                )

        if tokenizer is not None:
            for chunk in chunks:
                count = len(
                    tokenizer(
                        chunk.get("text", ""),
                        add_special_tokens=True,
                        truncation=False,
                    )["input_ids"]
                )
                if count > 512:
                    manifest_stats["oversize_chunks"].append(
                        {
                            "document_id": str(document.id),
                            "chunk_id": chunk.get("chunk_id"),
                            "tokens": count,
                            "chars": len(chunk.get("text", "")),
                        }
                    )

    index_stats = {
        "checked": check_index,
        "counts_match": 0,
        "counts_mismatch": [],
        "stored_vectors": 0,
    }
    if check_index:
        settings = get_settings()
        stores = {
            "legacy": MilvusStore(uri=settings.milvus_uri),
            "hybrid": HybridStore(uri=settings.milvus_uri),
        }
        try:
            for document in active:
                path = manifest_path(document.active_manifest)
                if path is None or not path.is_file():
                    continue
                manifest = json.loads(path.read_text(encoding="utf-8"))
                store = stores[
                    "hybrid" if manifest.get("collection_name") == "rag_bge_large_zh_v15_hybrid_v2"
                    else "legacy"
                ]
                actual = store.count_snapshot(
                    str(document.knowledge_base_id),
                    str(document.id),
                    document.active_snapshot_id,
                )
                index_stats["stored_vectors"] += actual
                expected = manifest["chunk_count"]
                if actual == expected:
                    index_stats["counts_match"] += 1
                else:
                    index_stats["counts_mismatch"].append(
                        {
                            "document_id": str(document.id),
                            "expected": expected,
                            "actual": actual,
                        }
                    )
        finally:
            for store in stores.values():
                store.close()

    source_report = None
    if folder:
        sources = sorted(
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED
        )
        source_hashes = {digest(path): path for path in sources}
        present_hashes = set(by_hash)
        source_report = {
            "source_files": len(sources),
            "unique_source_contents": len(source_hashes),
            "matched_unique_contents": len(set(source_hashes) & present_hashes),
            "missing_from_database": [
                path.relative_to(folder).as_posix()
                for sha, path in source_hashes.items()
                if sha not in present_hashes
            ],
            "database_files_outside_source_folder": [
                row.original_filename
                for sha, rows in by_hash.items()
                if sha not in source_hashes
                for row in rows
                if row.status != "deleting"
            ],
        }

    duplicates = [
        {
            "sha256": sha,
            "documents": [
                {
                    "id": str(document.id),
                    "filename": document.original_filename,
                    "status": document.status,
                }
                for document in rows
            ],
        }
        for sha, rows in by_hash.items()
        if len(rows) > 1
    ]
    terminal_failures = [
        {
            "document_id": str(document.id),
            "filename": document.original_filename,
            "error": document.error_message,
        }
        for document in documents
        if document.status == "failed"
    ]
    return {
        "knowledge_base_id": str(knowledge_base_id),
        "database_documents": len(documents),
        "unique_contents": len(by_hash),
        "duplicate_groups": len(duplicates),
        "duplicate_documents": sum(len(item["documents"]) - 1 for item in duplicates),
        "status_counts": dict(sorted(status_counts.items())),
        "active_snapshots": len(active),
        "failures": terminal_failures,
        "duplicates": duplicates,
        "source": source_report,
        "manifests": {
            **manifest_stats,
            "models": dict(manifest_stats["models"]),
            "collections": dict(manifest_stats["collections"]),
            "chunkers": {
                str(key): value for key, value in manifest_stats["chunkers"].items()
            },
        },
        "indexes": index_stats,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="只读核对语料、向量清单与当前索引")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-index", action="store_true")
    args = parser.parse_args()
    report = await collect(
        args.folder.resolve() if args.folder else None,
        args.knowledge_base_id,
        not args.skip_index,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    else:
        print(rendered)


if __name__ == "__main__":
    asyncio.run(main())
