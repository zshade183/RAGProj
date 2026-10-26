"""Explicit opt-in. Uses a UNIQUE TEMPORARY SCHEMA; never touches public tables.
PowerShell: $env:TEST_DATABASE_URL='postgresql+asyncpg://user:password@localhost/db'
"""

import asyncio
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from app.db.base import Base
from app.models import Document, KnowledgeBase, ProcessingJob
from app.services.documents import delete as deletion
from app.tasks import queue
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Set TEST_DATABASE_URL to run actual PostgreSQL locking tests",
)


@pytest_asyncio.fixture
async def db(monkeypatch):
    url = os.environ["TEST_DATABASE_URL"]
    schema = "rag_test_" + uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(queue, "async_session_factory", factory)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed(factory):
    async with factory() as session, session.begin():
        kb = KnowledgeBase(name="queue test")
        session.add(kb)
        await session.flush()
        doc = Document(
            knowledge_base_id=kb.id,
            original_filename="test.txt",
            content_type="text/plain",
            size_bytes=4,
            sha256="a" * 64,
            bucket_name="documents",
            object_name=uuid4().hex,
            status="uploaded",
            active_manifest="old.json",
            active_snapshot_id="b" * 64,
        )
        session.add(doc)
        await session.flush()
        await queue.enqueue(session, doc)
        return doc.id


@pytest.mark.asyncio
async def test_two_claimers_only_one_owner(db):
    await seed(db)
    claims = await asyncio.gather(queue.claim(), queue.claim())
    assert sum(task is not None for task in claims) == 1


@pytest.mark.asyncio
async def test_expiry_fences_old_worker_and_activates_only_new(db):
    doc_id = await seed(db)
    old = await queue.claim()
    async with db() as session, session.begin():
        job = await session.scalar(select(ProcessingJob))
        job.lease_until = func.now() - timedelta(seconds=1)
    new = await queue.claim()
    assert new["token"] != old["token"]
    assert not await queue.finish(
        old, result={"manifest_path": "bad.json", "snapshot_id": "c" * 64}
    )
    async with db() as session:
        assert (await session.get(Document, doc_id)).active_manifest == "old.json"
    assert await queue.finish(new, result={"manifest_path": "good.json", "snapshot_id": "d" * 64})
    async with db() as session:
        doc = await session.get(Document, doc_id)
        assert doc.status == "ready" and doc.active_manifest == "good.json"


@pytest.mark.asyncio
async def test_failed_attempt_preserves_old_snapshot_and_retries(db):
    doc_id = await seed(db)
    task = await queue.claim()
    assert await queue.finish(task, error="injected Milvus failure")
    async with db() as session:
        doc = await session.get(Document, doc_id)
        job = await session.scalar(select(ProcessingJob))
        assert doc.active_manifest == "old.json"
        assert job.status == "queued" and job.attempts == 1
    assert await queue.claim() is None  # backoff not elapsed


@pytest.mark.asyncio
async def test_enqueue_rolls_back_with_document_transaction(db):
    doc_id = await seed(db)
    async with db() as session, session.begin():
        await session.delete(await session.scalar(select(ProcessingJob)))
    async with db() as session:
        doc = await session.get(Document, doc_id)
        await queue.enqueue(session, doc)
        await session.rollback()
    async with db() as session:
        assert await session.scalar(select(ProcessingJob)) is None


@pytest.mark.asyncio
async def test_terminal_failure_manual_retry_resets_attempts(db):
    doc_id = await seed(db)
    task = await queue.claim()
    async with db() as session, session.begin():
        job = await session.scalar(select(ProcessingJob))
        job.attempts = queue.get_settings().worker_max_attempts
    await queue.finish(task, error="injected final failure")
    async with db() as session, session.begin():
        doc = await session.get(Document, doc_id)
        assert doc.status == "failed"
        job = await queue.enqueue(session, doc)
        assert job.attempts == 0 and job.status == "queued"


@pytest.mark.asyncio
async def test_delete_cancels_live_job_and_removes_document(db, monkeypatch):
    doc_id = await seed(db)
    task = await queue.claim()
    assert task is not None
    cleanup_calls = []
    monkeypatch.setattr(
        deletion,
        "_delete_objects",
        lambda document: cleanup_calls.append("objects") or 1,
    )
    monkeypatch.setattr(
        deletion,
        "_delete_vectors",
        lambda document_id: cleanup_calls.append("vectors") or 1,
    )
    monkeypatch.setattr(deletion, "_delete_attempt_artifacts", lambda document_id: None)
    async with db() as session:
        doc = await session.get(Document, doc_id)
        knowledge_base_id = doc.knowledge_base_id
    async with db() as session:
        await deletion.delete_document(
            knowledge_base_id=knowledge_base_id,
            document_id=doc_id,
            session=session,
        )
    async with db() as session:
        assert await session.get(Document, doc_id) is None
        assert await session.scalar(select(ProcessingJob)) is None
    assert cleanup_calls == ["objects", "vectors"]


@pytest.mark.asyncio
async def test_live_content_is_unique_per_knowledge_base(db):
    doc_id = await seed(db)
    async with db() as session:
        original = await session.get(Document, doc_id)
        duplicate = Document(
            knowledge_base_id=original.knowledge_base_id,
            original_filename="duplicate.txt",
            content_type="text/plain",
            size_bytes=4,
            sha256=original.sha256,
            bucket_name="documents",
            object_name=uuid4().hex,
            status="uploaded",
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            await session.flush()
