"""Dry-run-first cleanup of superseded vector snapshots and old artifact attempts."""

import argparse
import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models import Document
from app.services.retrieval.hybrid_store import HYBRID_COLLECTION, HybridStore
from app.services.retrieval.milvus_store import MilvusStore
from app.services.storage.minio_storage import get_storage
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]


def manifest_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


async def load_documents(knowledge_base_id: UUID) -> list[Document]:
    async with async_session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Document).where(
                        Document.knowledge_base_id == knowledge_base_id,
                        Document.active_snapshot_id.is_not(None),
                        Document.active_manifest.is_not(None),
                        Document.status != "deleting",
                    )
                )
            ).all()
        )


def candidates(document: Document, stores: dict[str, object], storage) -> dict:
    path = manifest_path(document.active_manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    store_key = (
        "hybrid"
        if manifest.get("collection_name") == HYBRID_COLLECTION
        else "legacy"
    )
    store = stores[store_key]
    snapshots = store.list_snapshot_ids(str(document.id))
    stale_snapshots = [
        snapshot for snapshot in snapshots if snapshot != document.active_snapshot_id
    ]

    prefix = f"artifacts/{document.id}/"
    objects = storage.list_prefix(
        bucket_name=document.bucket_name,
        prefix=prefix,
    )
    active_token = path.parent.name
    stale_objects = [
        name
        for name in objects
        if name[len(prefix) :].split("/", 1)[0] != active_token
    ]
    document_root = (ROOT / "storage" / "processed" / str(document.id)).resolve()
    processed_root = (ROOT / "storage" / "processed").resolve()
    stale_attempts = []
    if document_root.parent == processed_root and document_root.is_dir():
        stale_attempts = [
            item.name
            for item in document_root.iterdir()
            if item.is_dir() and item.name != active_token
        ]
    return {
        "document_id": str(document.id),
        "active_snapshot": document.active_snapshot_id,
        "stale_snapshots": stale_snapshots,
        "stores": [store_key] if stale_snapshots else [],
        "stale_objects": stale_objects,
        "stale_attempts": stale_attempts,
    }


def apply(document: Document, item: dict, stores: dict[str, object], storage) -> dict:
    path = manifest_path(document.active_manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    store_key = "hybrid" if manifest.get("collection_name") == HYBRID_COLLECTION else "legacy"
    store = stores[store_key]
    deleted_vectors = {}
    for snapshot in item["stale_snapshots"]:
        deleted_vectors[snapshot] = store.delete_snapshot(str(document.id), snapshot)
    remaining = store.list_snapshot_ids(str(document.id))
    expected = store.count_snapshot(
        str(document.knowledge_base_id),
        str(document.id),
        document.active_snapshot_id,
    )
    if remaining != [document.active_snapshot_id] or expected <= 0:
        raise RuntimeError("清理后当前快照校验失败")
    for object_name in item["stale_objects"]:
        storage.delete_file(
            bucket_name=document.bucket_name,
            object_name=object_name,
        )
    document_root = (ROOT / "storage" / "processed" / str(document.id)).resolve()
    for attempt in item["stale_attempts"]:
        target = (document_root / attempt).resolve()
        if target.parent != document_root or target.name != attempt:
            raise ValueError("旧尝试目录路径不安全")
        shutil.rmtree(target)
    return deleted_vectors


async def main() -> None:
    parser = argparse.ArgumentParser(description="回收旧快照和旧尝试产物，默认只报告")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    docs = await load_documents(args.knowledge_base_id)
    stores = {
        "legacy": MilvusStore(uri=settings.milvus_uri),
        "hybrid": HybridStore(uri=settings.milvus_uri),
    }
    storage = get_storage()
    report = {
        "knowledge_base_id": str(args.knowledge_base_id),
        "apply": args.apply,
        "documents": [],
        "errors": [],
    }
    try:
        for document in docs:
            try:
                item = candidates(document, stores, storage)
                if args.apply and (
                    item["stale_snapshots"]
                    or item["stale_objects"]
                    or item["stale_attempts"]
                ):
                    item["deleted_vectors"] = apply(document, item, stores, storage)
                report["documents"].append(item)
                print(
                    "CLEAN" if args.apply else "DRY_RUN",
                    document.original_filename,
                    "snapshots=",
                    len(item["stale_snapshots"]),
                    "objects=",
                    len(item["stale_objects"]),
                    "attempts=",
                    len(item["stale_attempts"]),
                    flush=True,
                )
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                report["errors"].append(
                    {"document_id": str(document.id), "error": type(exc).__name__}
                )
    finally:
        for store in stores.values():
            store.close()
    report_path = args.report or (
        Path("storage/gc-reports")
        / f"gc-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    stale_vectors = sum(len(item["stale_snapshots"]) for item in report["documents"])
    stale_objects = sum(len(item["stale_objects"]) for item in report["documents"])
    stale_attempts = sum(len(item["stale_attempts"]) for item in report["documents"])
    print(
        f"文档 {len(docs)}，旧快照 {stale_vectors}，旧对象 {stale_objects}，"
        f"旧尝试 {stale_attempts}，"
        f"错误 {len(report['errors'])}；报告 {report_path}"
    )


if __name__ == "__main__":
    asyncio.run(main())
