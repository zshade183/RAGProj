"""Run the original BGE-small/512 reference indexing and retrieval path."""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
from app.services.documents.chunking import build_chunks
from app.services.documents.semantic import markdown_page
from app.tasks.pipeline import text_document
from pymilvus import DataType, MilvusClient
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def load_chunks(folder: Path, knowledge_base_id: UUID):
    all_chunks = []
    files = sorted(folder.rglob("*.md"))
    for path in files:
        relative = path.relative_to(folder).as_posix()
        document_id = uuid5(NAMESPACE_URL, relative)
        sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        document = text_document(
            {"filename": relative, "sha256": sha256},
            [markdown_page(path)],
        )
        size = 600
        while True:
            chunks = build_chunks(
                document,
                knowledge_base_id,
                document_id,
                chunk_size=size,
                overlap=min(80, size // 4),
            )
            if max(len(chunk["text"]) for chunk in chunks) <= 512:
                break
            if size <= 32:
                raise ValueError(f"无法切块：{relative}")
            size = max(32, size // 2)
        all_chunks.extend(chunks)
    return files, all_chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="真实运行 BGE-small 512 维语料入库与检索")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--uri", default="http://127.0.0.1:19530")
    parser.add_argument("--question", default="EUV光源功率如何影响7nm以下制程？")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--download", action="store_true", help="允许从 Hugging Face 下载模型")
    parser.add_argument("--keep", action="store_true", help="保留验证集合，便于人工查询")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size 必须大于 0")

    started = time.monotonic()
    files, chunks = load_chunks(args.folder.resolve(), args.knowledge_base_id)
    if not chunks:
        raise ValueError("语料没有生成文本块")
    texts = [chunk["text"] for chunk in chunks]

    model = SentenceTransformer(
        args.model,
        device="cpu",
        cache_folder=str(ROOT / "storage" / "models"),
        trust_remote_code=False,
        local_files_only=not args.download,
    )
    dimension = int(model.get_embedding_dimension())
    if dimension != 512:
        raise RuntimeError(f"模型维度应为 512，实际为 {dimension}")
    max_tokens = min(int(model.max_seq_length), 512)
    for index, text in enumerate(texts):
        count = len(
            model.tokenizer(
                text,
                add_special_tokens=True,
                truncation=False,
            )["input_ids"]
        )
        if count > max_tokens:
            raise ValueError(f"第 {index} 个块 {count} tokens 超过 {max_tokens}")

    vectors = model.encode(
        texts,
        prompt="",
        batch_size=args.batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape != (len(chunks), 512) or not np.isfinite(vectors).all():
        raise RuntimeError("BGE-small 向量形状或数值异常")

    suffix = hashlib.sha256(
        f"{time.time_ns()}:{args.knowledge_base_id}".encode()
    ).hexdigest()[:12]
    collection = f"rag_bge_small_zh_v15_512_verify_{suffix}"
    client = MilvusClient(uri=args.uri, timeout=60)
    try:
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("chunk_id", DataType.VARCHAR, max_length=36)
        schema.add_field("source_filename", DataType.VARCHAR, max_length=1024)
        schema.add_field("page_number", DataType.INT64)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=512)
        indexes = client.prepare_index_params()
        indexes.add_index(
            field_name="vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        client.create_collection(
            collection_name=collection,
            schema=schema,
            index_params=indexes,
            consistency_level="Strong",
        )
        client.load_collection(collection_name=collection)
        for start in range(0, len(chunks), 128):
            rows = []
            for offset, (chunk, vector) in enumerate(
                zip(chunks[start : start + 128], vectors[start : start + 128], strict=True)
            ):
                rows.append(
                    {
                        "id": start + offset,
                        "chunk_id": chunk["chunk_id"],
                        "source_filename": chunk["source_filename"],
                        "page_number": chunk["page_number"],
                        "vector": vector.tolist(),
                    }
                )
            client.insert(collection_name=collection, data=rows)
        client.flush(collection_name=collection)

        query_text = QUERY_INSTRUCTION + args.question
        query_vector = model.encode(
            [query_text],
            prompt="",
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0]
        hits = client.search(
            collection_name=collection,
            data=[query_vector.tolist()],
            anns_field="vector",
            limit=5,
            output_fields=["chunk_id", "source_filename", "page_number"],
            search_params={"metric_type": "COSINE", "params": {"ef": 100}},
            consistency_level="Strong",
        )[0]
        if not hits:
            raise RuntimeError("BGE-small 检索没有返回结果")
    finally:
        if not args.keep and client.has_collection(collection_name=collection):
            client.drop_collection(collection_name=collection)
        client.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    vector_path = args.output.with_suffix(".vectors.npy")
    np.save(vector_path, vectors, allow_pickle=False)
    report = {
        "completed_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.monotonic() - started, 2),
        "model": args.model,
        "dimension": dimension,
        "max_tokens": max_tokens,
        "source_files": len(files),
        "chunks": len(chunks),
        "collection": collection,
        "collection_retained": args.keep,
        "vectors_file": str(vector_path),
        "vectors_sha256": hashlib.sha256(vector_path.read_bytes()).hexdigest(),
        "question": args.question,
        "hits": [
            {
                "chunk_id": hit["entity"]["chunk_id"],
                "filename": hit["entity"]["source_filename"],
                "page_number": hit["entity"]["page_number"],
                "score": float(hit["distance"]),
            }
            for hit in hits
        ],
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
