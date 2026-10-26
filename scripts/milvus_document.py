import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid5

import numpy as np
from app.db.session import engine
from app.models.document import Document
from app.services.embeddings.bge import (
    MODEL_NAME,
    QUERY_INSTRUCTION,
    BgeEmbedding,
)
from app.services.retrieval.milvus_store import MilvusStore
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def verify_database_document(manifest: dict) -> None:
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(
                    Document.knowledge_base_id,
                    Document.sha256,
                    Document.status,
                ).where(Document.id == UUID(manifest["document_id"]))
            )
            row = result.mappings().one_or_none()

            if row is None:
                raise ValueError("PostgreSQL 中不存在这份文档。")

            if str(row["knowledge_base_id"]) != manifest["knowledge_base_id"]:
                raise ValueError("知识库 ID 与数据库记录不一致。")

            if row["sha256"] != manifest["source_sha256"]:
                raise ValueError("文件哈希与数据库记录不一致。")

            if row["status"] == "deleting":
                raise ValueError("文档正在删除，不能执行入库或检索。")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="将向量写入 Milvus，或验证已入库数据的检索。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=["index", "search"],
        required=True,
    )
    parser.add_argument(
        "--uri",
        default="http://127.0.0.1:19530",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))

    if manifest.get("stage") != "embedded":
        raise ValueError("输入必须是 embedding-manifest.json。")

    if (
        manifest["model_name"] != MODEL_NAME
        or manifest["dimension"] != 1024
        or manifest["query_instruction"] != QUERY_INSTRUCTION
        or manifest.get("normalized") is not True
    ):
        raise ValueError("向量模型、维度或归一化设置不匹配。")

    kb_id = str(UUID(manifest["knowledge_base_id"]))
    doc_id = str(UUID(manifest["document_id"]))
    snapshot_id = hashlib.sha256(raw).hexdigest()
    chunks = manifest["chunks"]

    if not chunks or len(chunks) != manifest["chunk_count"]:
        raise ValueError("文本块数量不符合清单。")

    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("存在重复的文本块 ID。")

    for chunk in chunks:
        UUID(chunk["chunk_id"])

        if (
            chunk["knowledge_base_id"] != kb_id
            or chunk["document_id"] != doc_id
            or chunk["source_sha256"] != manifest["source_sha256"]
        ):
            raise ValueError("文本块归属或来源哈希不一致。")

        actual_hash = hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest()

        if actual_hash != chunk["content_sha256"]:
            raise ValueError("文本内容与哈希不一致。")

    asyncio.run(verify_database_document(manifest))

    store = MilvusStore(uri=args.uri)

    try:
        store.ensure_collection()

        if args.mode == "index":
            vector_path = (manifest_path.parent / manifest["vector_file"]).resolve()

            if not vector_path.is_relative_to(manifest_path.parent):
                raise ValueError("向量文件不在清单目录内。")

            actual_hash = hashlib.sha256(vector_path.read_bytes()).hexdigest()

            if actual_hash != manifest["vector_file_sha256"]:
                raise ValueError("向量文件哈希校验失败。")

            vectors = np.load(vector_path, allow_pickle=False)

            if vectors.shape != (len(chunks), 1024):
                raise ValueError(f"向量形状异常：{vectors.shape}")

            if not np.isfinite(vectors).all():
                raise ValueError("向量包含无效数值。")

            if not np.allclose(
                np.linalg.norm(vectors, axis=1),
                1.0,
                atol=1e-4,
            ):
                raise ValueError("向量未正确归一化。")

            rows = []

            for chunk, vector in zip(chunks, vectors, strict=True):
                filename = chunk["source_filename"]
                text = chunk["text"]

                # Milvus VARCHAR 限制按 UTF-8 字节数检查。
                if len(filename.encode("utf-8")) > 1024:
                    raise ValueError("文件名超过字段长度限制。")

                if len(text.encode("utf-8")) > 16384:
                    raise ValueError("文本超过字段长度限制。")

                rows.append(
                    {
                        "id": str(
                            uuid5(
                                UUID(chunk["chunk_id"]),
                                snapshot_id,
                            )
                        ),
                        "chunk_id": chunk["chunk_id"],
                        "knowledge_base_id": kb_id,
                        "document_id": doc_id,
                        "snapshot_id": snapshot_id,
                        "source_filename": filename,
                        "page_number": chunk["page_number"],
                        "text": text,
                        "vector": vector.tolist(),
                    }
                )

            store.upsert(rows)

        count = store.count_snapshot(kb_id, doc_id, snapshot_id)
        print(f"当前快照记录数：{count}，预期：{len(chunks)}")

        if count != len(chunks):
            raise RuntimeError("入库数量不完整，请使用 index 模式重新执行。")

        if args.mode == "index":
            print("入库完成；可使用 search 模式验证数据库检索。")
            return

        embedder = BgeEmbedding(cache_dir=PROJECT_ROOT / "storage" / "models")

        print("输入问题开始检索，输入 exit 退出。")

        while True:
            try:
                question = input("\n问题：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出。")
                break

            if question.lower() == "exit":
                break

            if not question:
                continue

            try:
                vector = embedder.encode_query(question)
            except ValueError as exc:
                print(exc)
                continue

            hits = store.search(
                vector=vector.tolist(),
                knowledge_base_id=kb_id,
                document_id=doc_id,
                snapshot_id=snapshot_id,
            )

            for rank, hit in enumerate(hits, start=1):
                entity = hit["entity"]
                print(f"\n[{rank}] 第 {entity['page_number']} 页 | 相似度 {hit['distance']:.4f}")
                print(entity["source_filename"])
                print(entity["text"])

    finally:
        store.close()


if __name__ == "__main__":
    main()
