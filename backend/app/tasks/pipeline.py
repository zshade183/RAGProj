"""One isolated attempt. Never updates active DB snapshot; parent publishes after fencing."""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[3]


def write_json(path, value):
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    # Windows can briefly deny replacement while another thread is reading.
    for attempt in range(20):
        tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        try:
            tmp.replace(path)
            return
        except PermissionError:
            tmp.unlink(missing_ok=True)
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def text_document(task, pages):
    return {
        "schema_version": 1,
        "stage": "merged",
        "source_filename": task["filename"],
        "source_sha256": task["sha256"],
        "page_count": len(pages),
        "pages": pages,
    }


def make_page(number, text):
    return {
        "page_number": number,
        "blocks": [
            {
                "block_id": f"p{number:04d}-b0001",
                "source": "native",
                "kind": "text",
                "text": text,
                "include_in_retrieval": True,
            }
        ],
    }


def parse(task, source, folder, settings, progress):
    """PPTX/PDF/DOCX get rendered OCR; text/HTML use logical pages, not print page numbers."""
    suffix = source.suffix.lower()
    if suffix in {".txt", ".md", ".html"}:
        if suffix == ".md":
            from app.services.documents.semantic import markdown_page

            result = text_document(task, [markdown_page(source)])
            result["page_number_kind"] = "logical_document"
            return result
        text = source.read_text(encoding="utf-8-sig", errors="strict")
        if suffix == ".html":
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            for node in soup(["script", "style", "noscript"]):
                node.decompose()
            text = soup.get_text("\n", strip=True)
        result = text_document(task, [make_page(1, text)])
        result["page_number_kind"] = "logical_document"
        return result

    if suffix not in {".pptx", ".pdf", ".docx"}:
        raise ValueError(f"不支持的文档类型：{suffix}")

    import pymupdf
    from pptx import Presentation

    from scripts.ocr_pages import parse_tsv, run_tesseract
    from scripts.render_pptx import convert_to_pdf, render_pdf

    progress("rendering")
    if suffix == ".pptx":
        expected_pages = len(Presentation(str(source)).slides)
        pdf = convert_to_pdf(source, folder, Path(settings.libreoffice_path), 300)
    elif suffix == ".docx":
        # DOCX print page numbers refer to LibreOffice's rendered PDF.
        profile = folder / "lo-profile"
        subprocess.run(
            [
                settings.libreoffice_path,
                f"-env:UserInstallation={profile.as_uri()}",
                "--headless",
                "--norestore",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(folder),
                str(source),
            ],
            check=True,
            timeout=300,
        )
        pdf = folder / f"{source.stem}.pdf"
        with pymupdf.open(pdf) as document:
            expected_pages = document.page_count
    elif suffix == ".pdf":
        pdf = source
        with pymupdf.open(pdf) as document:
            if document.needs_pass:
                raise ValueError("加密 PDF 需要先解密后上传")
            expected_pages = document.page_count
    else:
        raise ValueError(f"不支持的文档类型：{suffix}")
    if expected_pages < 1:
        raise ValueError("文档没有页面")
    pages = render_pdf(pdf, folder, expected_pages, 200)
    if settings.rag_use_unstructured:
        from app.services.documents.unstructured_client import parse_pdf

        progress("merging")
        return parse_pdf(pdf, task, settings.unstructured_api_url, expected_pages)
    check = subprocess.run(
        [settings.tesseract_path, "--list-langs"], capture_output=True, timeout=30, check=True
    )
    installed = set(check.stdout.decode("utf-8", errors="replace").splitlines())
    missing = set(settings.ocr_languages.split("+")) - {line.strip() for line in installed}
    if missing:
        raise ValueError(f"OCR 缺少语言包：{sorted(missing)}")
    progress("ocr")
    ocr_dir = folder / "ocr"
    ocr_dir.mkdir()
    records = []
    for page in pages:
        progress("ocr", page=page["page_number"], total=len(pages))
        result = run_tesseract(
            Path(settings.tesseract_path), folder / page["image_path"], settings.ocr_languages, 180
        )
        prefix = f"page-{page['page_number']:04d}"
        (ocr_dir / f"{prefix}.stderr.log").write_bytes(result.stderr)
        if result.returncode:
            raise RuntimeError(f"OCR 第 {page['page_number']} 页失败，查看该页 stderr 日志")
        (ocr_dir / f"{prefix}.tsv").write_bytes(result.stdout)
        words, lines = parse_tsv(result.stdout.decode("utf-8-sig"))
        write_json(
            ocr_dir / f"{prefix}.json",
            {
                "page_number": page["page_number"],
                "image_sha256": page["sha256"],
                "width_px": page["width_px"],
                "height_px": page["height_px"],
                "words": words,
                "lines": lines,
                "no_text_detected": not words,
            },
        )
        records.append(
            {
                "page_number": page["page_number"],
                "status": "succeeded",
                "result_file": f"{prefix}.json",
            }
        )
    manifest = ocr_dir / "ocr-manifest.json"
    write_json(
        manifest,
        {
            "stage": "ocr_completed",
            "failed_pages": 0,
            "source_sha256": task["sha256"],
            "page_count": len(pages),
            "pages": records,
        },
    )
    progress("merging")
    if suffix == ".pptx":
        from scripts.merge_pptx_ocr import merge

        result = json.loads(merge(source, manifest).read_text(encoding="utf-8"))
        result["source_filename"] = task["filename"]
        return result
    merged_pages = []
    with pymupdf.open(pdf) as document:
        for index, record in enumerate(records):
            page = make_page(index + 1, document[index].get_text(sort=True))
            ocr = json.loads((ocr_dir / record["result_file"]).read_text(encoding="utf-8"))
            for line in ocr["lines"]:
                page["blocks"].append(
                    {
                        "block_id": f"p{index + 1:04d}-b{len(page['blocks']) + 1:04d}",
                        "source": "ocr",
                        "kind": "text",
                        "text": line["text_preview"],
                        "bbox_px": line["bbox_px"],
                        "include_in_retrieval": True,
                        "words": [ocr["words"][i] for i in line["word_indices"]],
                    }
                )
            merged_pages.append(page)
    result = text_document(task, merged_pages)
    result["page_number_kind"] = "rendered_docx" if suffix == ".docx" else "pdf_page"
    result["limitations"] = ["PDF/DOCX 表格与复杂版面未重建为结构化表格，保留渲染页供核对"]
    return result


def run(task, folder, embedder=None):
    from app.core.config import get_settings
    from app.services.storage.minio_storage import get_storage
    from scripts.filter_ocr_quality import apply_policy

    settings = get_settings()
    storage = get_storage()

    def progress(stage, **extra):
        write_json(folder / "progress.json", {"stage": stage, **extra})
        print(stage, extra, flush=True)

    progress("download")
    source = folder / ("source" + Path(task["filename"]).suffix.lower())
    storage.download_file(
        object_name=task["object_name"], bucket_name=task["bucket_name"], file_path=source
    )
    if digest(source) != task["sha256"]:
        raise ValueError("原文件哈希校验失败")
    document = parse(task, source, folder, settings, progress)
    write_json(folder / "merged-document.json", document)
    document = apply_policy(document)
    write_json(folder / "quality-reviewed-document.json", document)
    progress("parsed")

    import numpy as np

    from app.services.documents.chunking import CHUNKER_VERSION, build_chunks
    from app.services.embeddings.bge import MODEL_NAME, QUERY_INSTRUCTION, BgeEmbedding
    from app.services.retrieval.hybrid_store import HYBRID_COLLECTION, HybridStore, snapshot_rows

    progress("embedding")
    if embedder is None:
        embedder = BgeEmbedding(
            ROOT / "storage" / "models",
            device=settings.worker_embedding_device,
                local_files_only=getattr(settings, "model_local_files_only", True),
        )
    size, overlap = 400, 60
    chunker_version = CHUNKER_VERSION
    semantic_embedding_provider = "bge"
    semantic_embedding_model = MODEL_NAME
    semantic_embedder = None
    if settings.rag_semantic_chunking:
        from app.services.documents.semantic import SEMANTIC_VERSION, semantic_chunks

        semantic_embedding_provider = settings.rag_semantic_embedding_provider
        if semantic_embedding_provider == "openai":
            from app.services.embeddings.openai_compatible import (
                OpenAICompatibleEmbeddings,
            )

            key = settings.openai_embedding_api_key
            semantic_embedder = OpenAICompatibleEmbeddings(
                base_url=settings.openai_embedding_base_url,
                api_key=key.get_secret_value() if key is not None else "",
                model=settings.openai_embedding_model,
            )
            semantic_embedding_model = settings.openai_embedding_model
        try:
            chunks = semantic_chunks(
                document,
                UUID(task["knowledge_base_id"]),
                UUID(task["document_id"]),
                embedder,
                semantic_embedder=semantic_embedder,
                openai_threshold=settings.rag_semantic_openai_threshold
                if semantic_embedding_provider == "openai"
                else None,
            )
        finally:
            if semantic_embedder is not None:
                semantic_embedder.close()
        chunker_version = SEMANTIC_VERSION
        size, overlap = None, 0
    else:
        while True:
            overlap = min(60, size // 4)
            chunks = build_chunks(
                document, UUID(task["knowledge_base_id"]), UUID(task["document_id"]), size, overlap
            )
            if max(embedder.token_count(c["text"]) for c in chunks) <= embedder.max_tokens:
                break
            if size <= 32:
                raise ValueError("文本不能在模型预算内切块")
            size = max(32, size // 2)
    for chunk in chunks:
        chunk["token_count"] = embedder.token_count(chunk["text"])
    vectors = embedder.encode_documents([c["text"] for c in chunks], batch_size=2)
    np.save(folder / "vectors.npy", vectors, allow_pickle=False)
    manifest = {
        "schema_version": 1,
        "stage": "embedded",
        "model_name": MODEL_NAME,
        "query_instruction": QUERY_INSTRUCTION,
        "dimension": 1024,
        "normalized": True,
        "knowledge_base_id": task["knowledge_base_id"],
        "document_id": task["document_id"],
        "source_sha256": task["sha256"],
        "chunk_count": len(chunks),
        "chunks": chunks,
        "chunker_version": chunker_version,
        "semantic_embedding_provider": semantic_embedding_provider,
        "semantic_embedding_model": semantic_embedding_model,
        "semantic_openai_threshold": settings.rag_semantic_openai_threshold
        if semantic_embedding_provider == "openai"
        else None,
        "collection_name": HYBRID_COLLECTION,
        "chunk_size_chars": size,
        "overlap_chars": overlap,
        "vector_file": "vectors.npy",
        "vector_file_sha256": digest(folder / "vectors.npy"),
        "attempt_token": task["token"],
    }
    manifest_path = folder / "embedding-manifest.json"
    write_json(manifest_path, manifest)
    snapshot_id = digest(manifest_path)
    progress("indexing")
    store = HybridStore(uri=settings.milvus_uri)
    try:
        store.ensure_collection()
        rows = snapshot_rows(manifest, snapshot_id, vectors)
        store.upsert(rows)
        if store.count_snapshot(task["knowledge_base_id"], task["document_id"], snapshot_id) != len(
            chunks
        ):
            raise RuntimeError("向量写入数量不完整")
    finally:
        store.close()
    # Preserve artifacts in object storage before activating the snapshot.
    progress("publish")
    for file in sorted(folder.rglob("*")):
        if (
            file.is_file()
            and file.name not in {"pipeline.log", "progress.json", "task.json"}
            and "lo-profile" not in file.parts
        ):
            storage.upload_file(
                object_name=f"artifacts/{task['document_id']}/{task['token']}/{file.relative_to(folder).as_posix()}",
                file_path=file,
            )
    write_json(
        folder / "result.json",
        {
            "manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            "snapshot_id": snapshot_id,
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    args = parser.parse_args()
    folder = args.task.resolve().parent
    try:
        run(json.loads(args.task.read_text(encoding="utf-8")), folder)
    except Exception as exc:
        # Detailed exception stays in local log; API only exposes stage and error type.
        write_json(folder / "failure.json", {"error": type(exc).__name__})
        raise


if __name__ == "__main__":
    main()
