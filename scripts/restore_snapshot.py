"""Restore only a migration's unchanged active pointer; never remove indexes."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.db.session import async_session_factory
from app.models import Document, ProcessingJob
from app.services.rag.api_service import load_snapshot
from sqlalchemy import select


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    record = json.loads(args.record.read_text(encoding="utf-8"))
    doc_id = UUID(record["document_id"])
    load_snapshot(doc_id, record["old_manifest"], record["old_snapshot"])
    async with async_session_factory() as session, session.begin():
        doc = await session.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        job = await session.scalar(
            select(ProcessingJob).where(ProcessingJob.document_id == doc_id).with_for_update()
        )
        if (
            not doc
            or doc.active_snapshot_id != record["new_snapshot"]
            or doc.active_manifest != record["new_manifest"]
            or doc.status == "deleting"
            or (job and job.status in {"queued", "running"})
        ):
            raise RuntimeError("文档已变化或正在处理，拒绝覆盖")
        doc.active_manifest = record["old_manifest"]
        doc.active_snapshot_id = record["old_snapshot"]
        doc.status = record["old_status"]
    print("已恢复旧快照指针，新旧索引均保留")


if __name__ == "__main__":
    asyncio.run(main())
