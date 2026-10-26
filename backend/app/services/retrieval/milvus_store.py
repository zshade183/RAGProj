from uuid import UUID

from pymilvus import DataType, MilvusClient

COLLECTION_NAME = "rag_bge_large_zh_v15_1024_v1"


class MilvusStore:
    def __init__(
        self,
        uri: str = "http://127.0.0.1:19530",
    ) -> None:
        self.client = MilvusClient(uri=uri, timeout=30)

    def close(self) -> None:
        self.client.close()

    def ensure_collection(self) -> None:
        if not self.client.has_collection(collection_name=COLLECTION_NAME):
            schema = self.client.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
            )

            schema.add_field(
                field_name="id",
                datatype=DataType.VARCHAR,
                is_primary=True,
                max_length=36,
            )

            for name, length in (
                ("chunk_id", 36),
                ("knowledge_base_id", 36),
                ("document_id", 36),
                ("snapshot_id", 64),
                ("source_filename", 1024),
                ("text", 16384),
            ):
                schema.add_field(
                    field_name=name,
                    datatype=DataType.VARCHAR,
                    max_length=length,
                )

            schema.add_field(
                field_name="page_number",
                datatype=DataType.INT64,
            )
            schema.add_field(
                field_name="vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=1024,
            )

            index_params = self.client.prepare_index_params()
            index_params.add_index(
                field_name="vector",
                index_type="HNSW",
                metric_type="COSINE",
                params={
                    "M": 16,
                    "efConstruction": 200,
                },
            )

            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                schema=schema,
                index_params=index_params,
                consistency_level="Strong",
            )

        self.client.load_collection(collection_name=COLLECTION_NAME)

    def upsert(self, rows: list[dict]) -> None:
        for start in range(0, len(rows), 64):
            batch = rows[start : start + 64]

            self.client.upsert(
                collection_name=COLLECTION_NAME,
                data=batch,
            )

            print(
                f"已写入 {min(start + 64, len(rows))}/{len(rows)}",
                flush=True,
            )

        self.client.flush(collection_name=COLLECTION_NAME)

    @staticmethod
    def document_snapshot_filter(
        document_id: str,
        snapshot_id: str,
    ) -> str:
        doc_id = str(UUID(document_id))

        if len(snapshot_id) != 64 or any(c not in "0123456789abcdef" for c in snapshot_id):
            raise ValueError("snapshot_id 必须是 SHA-256 字符串。")

        return f'document_id == "{doc_id}" and snapshot_id == "{snapshot_id}"'

    @staticmethod
    def snapshot_filter(
        knowledge_base_id: str,
        document_id: str,
        snapshot_id: str,
    ) -> str:
        # UUID 和十六进制校验，避免把任意输入拼进表达式。
        kb_id = str(UUID(knowledge_base_id))
        return (
            f'knowledge_base_id == "{kb_id}" and '
            + MilvusStore.document_snapshot_filter(document_id, snapshot_id)
        )

    def count_snapshot(
        self,
        knowledge_base_id: str,
        document_id: str,
        snapshot_id: str,
    ) -> int:
        result = self.client.query(
            collection_name=COLLECTION_NAME,
            filter=self.snapshot_filter(
                knowledge_base_id,
                document_id,
                snapshot_id,
            ),
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        return int(result[0]["count(*)"])

    def delete_document(self, document_id: str) -> int:
        """Delete every stored version for one document, never by a loose prefix."""

        doc_id = str(UUID(document_id))
        expression = f'document_id == "{doc_id}"'
        if not self.client.has_collection(collection_name=COLLECTION_NAME):
            return 0
        before = self.client.query(
            collection_name=COLLECTION_NAME,
            filter=expression,
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        total = int(before[0]["count(*)"])
        if total:
            self.client.delete(
                collection_name=COLLECTION_NAME,
                filter=expression,
                consistency_level="Strong",
            )
            self.client.flush(collection_name=COLLECTION_NAME)
        return total

    def list_snapshot_ids(self, document_id: str) -> list[str]:
        doc_id = str(UUID(document_id))
        if not self.client.has_collection(collection_name=COLLECTION_NAME):
            return []
        rows = self.client.query(
            collection_name=COLLECTION_NAME,
            filter=f'document_id == "{doc_id}"',
            output_fields=["snapshot_id"],
            limit=16384,
            consistency_level="Strong",
        )
        return sorted({row["snapshot_id"] for row in rows})

    def delete_snapshot(self, document_id: str, snapshot_id: str) -> int:
        expression = self.document_snapshot_filter(document_id, snapshot_id)
        if not self.client.has_collection(collection_name=COLLECTION_NAME):
            return 0
        before = self.client.query(
            collection_name=COLLECTION_NAME,
            filter=expression,
            output_fields=["count(*)"],
            consistency_level="Strong",
        )
        total = int(before[0]["count(*)"])
        if total:
            self.client.delete(
                collection_name=COLLECTION_NAME,
                filter=expression,
                consistency_level="Strong",
            )
            self.client.flush(collection_name=COLLECTION_NAME)
        return total

    def search(
        self,
        vector: list[float],
        knowledge_base_id: str,
        document_id: str,
        snapshot_id: str,
        limit: int = 5,
    ) -> list[dict]:
        if len(vector) != 1024:
            raise ValueError("查询向量必须是 1024 维。")

        if not 1 <= limit <= 50:
            raise ValueError("limit 必须在 1 到 50 之间。")

        results = self.client.search(
            collection_name=COLLECTION_NAME,
            data=[vector],
            anns_field="vector",
            filter=self.snapshot_filter(
                knowledge_base_id,
                document_id,
                snapshot_id,
            ),
            limit=limit,
            search_params={
                "metric_type": "COSINE",
                "params": {"ef": 64},
            },
            output_fields=[
                "chunk_id",
                "source_filename",
                "page_number",
                "text",
            ],
            consistency_level="Strong",
        )
        return results[0]
