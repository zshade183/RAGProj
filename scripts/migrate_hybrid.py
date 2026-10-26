"""Copy active embeddings into v2; activate only after full write and DB fencing."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models import Document, ProcessingJob
from app.services.rag.api_service import PROJECT_ROOT, load_snapshot
from app.services.retrieval.hybrid_store import HYBRID_COLLECTION, HybridStore, snapshot_rows
from app.services.storage.minio_storage import get_storage
from sqlalchemy import select


def copy_index(manifest, sid, path):
    if manifest.get("collection_name") == HYBRID_COLLECTION:
        store = HybridStore(uri=get_settings().milvus_uri)
        try:
            store.ensure_collection()
            if (
                store.count_snapshot(manifest["knowledge_base_id"], manifest["document_id"], sid)
                != manifest["chunk_count"]
            ):
                raise ValueError("当前混合索引缺少记录，请重新处理文档")
        finally:
            store.close()
        return path, sid
    original = Path(path)
    if not original.is_absolute():
        original = PROJECT_ROOT / original
    vector_file = (original.parent / manifest["vector_file"]).resolve()
    if not vector_file.is_relative_to(PROJECT_ROOT):
        raise ValueError("向量路径超出项目")
    raw = vector_file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["vector_file_sha256"]:
        raise ValueError("向量文件校验失败")
    vectors = np.load(vector_file, allow_pickle=False)
    if vectors.shape != (manifest["chunk_count"], 1024) or not np.isfinite(vectors).all():
        raise ValueError("向量形状或数值无效")
    new = {**manifest, "collection_name": HYBRID_COLLECTION, "migrated_from": sid}
    new_path = original.parent / f"embedding-hybrid-{uuid4().hex}.json"
    new_path.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    new_sid = hashlib.sha256(new_path.read_bytes()).hexdigest()
    store = HybridStore(uri=get_settings().milvus_uri)
    try:
        store.ensure_collection()
        store.upsert(snapshot_rows(new, new_sid, vectors))
        if store.count_snapshot(new["knowledge_base_id"], new["document_id"], new_sid) != len(
            vectors
        ):
            raise RuntimeError("混合索引写入不完整，旧快照保持生效")
    finally:
        store.close()
    get_storage().upload_file(
        object_name=f"artifacts/{new['document_id']}/migration/{new_path.name}", file_path=new_path
    )
    return new_path.relative_to(PROJECT_ROOT).as_posix(), new_sid


async def migrate(doc_id):
    async with async_session_factory() as session:
        doc = await session.get(Document, doc_id)
        if not doc or doc.status in {"uploading", "deleting"}:
            raise ValueError("文档不存在或状态不允许迁移")
        before_path, before_sid, sha = doc.active_manifest, doc.active_snapshot_id, doc.sha256
        before_status = doc.status
        kb_id = str(doc.knowledge_base_id)
        await session.rollback()
    manifest, sid = load_snapshot(doc_id, before_path, before_sid)
    if manifest["source_sha256"] != sha or manifest["knowledge_base_id"] != kb_id:
        raise ValueError("快照不属于当前文档")
    if before_path:
        path = before_path
    else:
        path = json.loads((PROJECT_ROOT / "rag_sources.json").read_text(encoding="utf-8"))[
            str(doc_id)
        ]
    new_path, new_sid = await asyncio.to_thread(copy_index, manifest, sid, path)
    backup = PROJECT_ROOT / "storage" / "index-migration-backups" / f"{doc_id}-{uuid4().hex}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(
        json.dumps(
            {
                "document_id": str(doc_id),
                "old_manifest": before_path,
                "old_snapshot": before_sid,
                "old_status": before_status,
                "new_manifest": new_path,
                "new_snapshot": new_sid,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    async with async_session_factory() as session, session.begin():
        doc = await session.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        if (
            not doc
            or doc.active_manifest != before_path
            or doc.active_snapshot_id != before_sid
            or doc.sha256 != sha
            or doc.status in {"uploading", "deleting"}
        ):
            raise RuntimeError("迁移期间文档发生变化，未切换快照；请重试")
        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == doc_id).with_for_update()
        )
        if job and job.status in {"queued", "running"}:
            raise RuntimeError("文档正在处理，未切换快照；请等待处理结束")
        doc.active_manifest, doc.active_snapshot_id, doc.status = new_path, new_sid, "ready"
    print(f"已迁移 {doc_id}；旧索引保留；分块未改变；回滚记录：{backup}")


async def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--document-id", type=UUID)
    group.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.document_id:
        ids = [args.document_id]
    else:
        async with async_session_factory() as session:
            ids = list(
                (
                    await session.scalars(
                        select(Document.id).where(
                            Document.active_manifest.is_not(None), Document.status != "deleting"
                        )
                    )
                ).all()
            )
        registry = PROJECT_ROOT / "rag_sources.json"
        if registry.exists():
            ids = list(
                dict.fromkeys(
                    ids + [UUID(k) for k in json.loads(registry.read_text(encoding="utf-8"))]
                )
            )
    for doc_id in ids:
        await migrate(doc_id)


if __name__ == "__main__":
    asyncio.run(main())
