"""Versioned Milvus BM25 + dense index. Legacy collections are never dropped."""

from uuid import UUID

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, RRFRanker

from app.services.retrieval.milvus_store import MilvusStore

HYBRID_COLLECTION = "rag_bge_large_zh_v15_hybrid_v2"
FIELDS = [
    "chunk_id",
    "knowledge_base_id",
    "document_id",
    "snapshot_id",
    "source_filename",
    "page_number",
    "text",
]


class HybridStore(MilvusStore):
    def ensure_collection(self):
        c = self.client
        if not c.has_collection(collection_name=HYBRID_COLLECTION):
            schema = c.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=36)
            for name, size in [
                ("chunk_id", 36),
                ("knowledge_base_id", 36),
                ("document_id", 36),
                ("snapshot_id", 64),
                ("source_filename", 1024),
            ]:
                schema.add_field(name, DataType.VARCHAR, max_length=size)
            schema.add_field(
                "text",
                DataType.VARCHAR,
                max_length=16384,
                enable_analyzer=True,
                analyzer_params={"tokenizer": "jieba"},
            )
            schema.add_field("page_number", DataType.INT64)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=1024)
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(
                Function(
                    name="text_bm25",
                    input_field_names=["text"],
                    output_field_names=["sparse"],
                    function_type=FunctionType.BM25,
                )
            )
            indexes = c.prepare_index_params()
            indexes.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            indexes.add_index(
                field_name="sparse",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
                params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
            )
            c.create_collection(
                collection_name=HYBRID_COLLECTION,
                schema=schema,
                index_params=indexes,
                consistency_level="Strong",
            )
        # A differently shaped existing collection is an error, not a reason to delete data.
        desc = c.describe_collection(collection_name=HYBRID_COLLECTION)
        fields = {f["name"]: f for f in desc["fields"]}
        if not set(FIELDS + ["vector", "sparse"]).issubset(fields):
            raise ValueError("混合索引结构不兼容")
        if int(fields["vector"].get("params", {}).get("dim", 0)) != 1024:
            raise ValueError("混合索引向量维度不兼容")
        c.load_collection(collection_name=HYBRID_COLLECTION)

    def upsert(self, rows):
        for start in range(0, len(rows), 64):
            self.client.upsert(collection_name=HYBRID_COLLECTION, data=rows[start : start + 64])
        self.client.flush(collection_name=HYBRID_COLLECTION)

    def count_snapshot(self, knowledge_base_id, document_id, snapshot_id):
        result = self.client.query(
            collection_name=HYBRID_COLLECTION,
            filter=self.snapshot_filter(knowledge_base_id, document_id, snapshot_id),
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        return int(result[0]["count(*)"])

    def hybrid_search(self, question, vector, scopes, limit=20):
        if not scopes or not 1 <= limit <= 50 or len(vector) != 1024:
            raise ValueError("混合检索范围、维度或数量异常")
        # Identical active-snapshot allowlist on both paths; never query all stored versions.
        expr = " or ".join("(" + self.snapshot_filter(*scope) + ")" for scope in scopes)
        requests = [
            AnnSearchRequest(
                data=[vector],
                anns_field="vector",
                expr=expr,
                limit=limit,
                param={"metric_type": "COSINE", "params": {"ef": 100}},
            ),
            AnnSearchRequest(
                data=[question],
                anns_field="sparse",
                expr=expr,
                limit=limit,
                param={"metric_type": "BM25", "params": {}},
            ),
        ]
        return self.client.hybrid_search(
            collection_name=HYBRID_COLLECTION,
            reqs=requests,
            ranker=RRFRanker(k=100),
            limit=limit,
            output_fields=FIELDS,
            consistency_level="Strong",
        )[0]

    def delete_document(self, document_id: str) -> int:
        from uuid import UUID

        doc_id = str(UUID(document_id))
        expression = f'document_id == "{doc_id}"'
        if not self.client.has_collection(collection_name=HYBRID_COLLECTION):
            return 0
        before = self.client.query(
            collection_name=HYBRID_COLLECTION,
            filter=expression,
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        total = int(before[0]["count(*)"])
        if total:
            self.client.delete(
                collection_name=HYBRID_COLLECTION,
                filter=expression,
                consistency_level="Strong",
            )
            self.client.flush(collection_name=HYBRID_COLLECTION)
        return total

    def list_snapshot_ids(self, document_id: str) -> list[str]:
        from uuid import UUID

        doc_id = str(UUID(document_id))
        if not self.client.has_collection(collection_name=HYBRID_COLLECTION):
            return []
        rows = self.client.query(
            collection_name=HYBRID_COLLECTION,
            filter=f'document_id == "{doc_id}"',
            output_fields=["snapshot_id"],
            limit=16384,
            consistency_level="Strong",
        )
        return sorted({row["snapshot_id"] for row in rows})

    def delete_snapshot(self, document_id: str, snapshot_id: str) -> int:
        doc_id = str(UUID(document_id))
        expression = self.document_snapshot_filter(doc_id, snapshot_id)
        if not self.client.has_collection(collection_name=HYBRID_COLLECTION):
            return 0
        before = self.client.query(
            collection_name=HYBRID_COLLECTION,
            filter=expression,
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        total = int(before[0]["count(*)"])
        if total:
            self.client.delete(
                collection_name=HYBRID_COLLECTION,
                filter=expression,
                consistency_level="Strong",
            )
            self.client.flush(collection_name=HYBRID_COLLECTION)
        return total


def snapshot_rows(manifest, snapshot_id, vectors):
    from uuid import uuid5

    chunks = manifest["chunks"]
    if len(chunks) != len(vectors):
        raise ValueError("向量数量不匹配")
    rows = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        rows.append(
            {
                "id": str(uuid5(UUID(chunk["chunk_id"]), snapshot_id)),
                **{
                    key: chunk[key]
                    for key in ["chunk_id", "source_filename", "page_number", "text"]
                },
                "knowledge_base_id": manifest["knowledge_base_id"],
                "document_id": manifest["document_id"],
                "snapshot_id": snapshot_id,
                "vector": vector.tolist(),
            }
        )
    return rows
