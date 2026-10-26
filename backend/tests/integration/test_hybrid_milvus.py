"""Opt-in real Milvus test; only creates and removes a unique test collection."""
import os
from uuid import uuid4

import numpy as np
import pytest
from app.services.retrieval import hybrid_store

pytestmark = pytest.mark.skipif(not os.getenv("TEST_MILVUS_URI"),
                                reason="Set TEST_MILVUS_URI for actual BM25/RRF integration")


def test_bm25_dense_active_snapshot_isolation(monkeypatch):
    collection = "rag_test_" + uuid4().hex
    monkeypatch.setattr(hybrid_store, "HYBRID_COLLECTION", collection)
    store = hybrid_store.HybridStore(os.environ["TEST_MILVUS_URI"])
    kb, doc, foreign, foreign_kb = str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
    vector = np.zeros(1024, dtype=np.float32)
    vector[0] = 1
    try:
        store.ensure_collection()
        rows = []
        for doc_id, sid, content in [(doc, "a" * 64, "字节代替下一步是行移位"),
                                     (doc, "b" * 64, "过期版本：字节代替"),
                                     (foreign, "a" * 64, "其他文档：字节代替")]:
            rows.append({"id": str(uuid4()), "chunk_id": str(uuid4()),
                         "knowledge_base_id": kb, "document_id": doc_id,
                         "snapshot_id": sid, "source_filename": "fixture.txt",
                         "page_number": 1, "text": content, "vector": vector.tolist()})
        rows.append(
            {
                "id": str(uuid4()),
                "chunk_id": str(uuid4()),
                "knowledge_base_id": foreign_kb,
                "document_id": doc,
                "snapshot_id": "a" * 64,
                "source_filename": "foreign-kb.txt",
                "page_number": 1,
                "text": "其他知识库的同名文档：字节代替",
                "vector": vector.tolist(),
            }
        )
        store.upsert(rows)
        assert store.count_snapshot(kb, doc, "a" * 64) == 1
        hits = store.hybrid_search("字节代替", vector.tolist(), [(kb, doc, "a" * 64)], 10)
        assert len(hits) == 1
        assert hits[0]["entity"]["document_id"] == doc
        assert hits[0]["entity"]["snapshot_id"] == "a" * 64
        assert store.list_snapshot_ids(doc) == ["a" * 64, "b" * 64]
        assert store.delete_snapshot(doc, "b" * 64) == 1
        assert store.list_snapshot_ids(doc) == ["a" * 64]
        assert store.delete_document(doc) == 2
        assert store.list_snapshot_ids(doc) == []
        assert store.list_snapshot_ids(foreign) == ["a" * 64]
    finally:
        if store.client.has_collection(collection_name=collection):
            store.client.drop_collection(collection_name=collection)
        store.close()
