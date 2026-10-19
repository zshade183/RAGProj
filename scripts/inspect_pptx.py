import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from app.db.session import async_session_factory, engine
from app.models.document import Document
from app.services.documents.pptx_parser import parse_pptx
from app.services.storage.minio_storage import get_storage
from starlette.concurrency import run_in_threadpool


def inspect_file(file_path: Path, expected_sha256: str) -> dict:
    """先检查文件完整性，再解析内容。"""

    digest = hashlib.sha256()

    with file_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)

    if digest.hexdigest() != expected_sha256:
        raise RuntimeError("文件 SHA-256 与数据库记录不一致，停止解析")

    return parse_pptx(file_path)


async def inspect_document(document_id: UUID) -> None:
    try:
        async with async_session_factory() as session:
            document = await session.get(Document, document_id)

            if document is None:
                raise ValueError("文档不存在")

            if Path(document.original_filename).suffix.lower() != ".pptx":
                raise ValueError("本次脚本只处理 PPTX 文件")

            if document.status != "uploaded":
                raise ValueError("本次检查要求文档状态为 uploaded")

            metadata = {
                "document_id": str(document.id),
                "knowledge_base_id": str(document.knowledge_base_id),
                "original_filename": document.original_filename,
                "sha256": document.sha256,
            }
            bucket_name = document.bucket_name
            object_name = document.object_name

        # 离开数据库会话后再执行较耗时的下载和解析。
        storage = get_storage()

        with TemporaryDirectory(prefix="rag-pptx-inspect-") as temp_dir:
            local_path = Path(temp_dir) / "source.pptx"

            await run_in_threadpool(
                storage.download_file,
                bucket_name=bucket_name,
                object_name=object_name,
                file_path=local_path,
            )

            result = await run_in_threadpool(
                inspect_file,
                local_path,
                metadata["sha256"],
            )

        result["source"] = metadata

        project_root = Path(__file__).resolve().parents[1]
        output_dir = project_root / "storage" / "parsed" / str(document_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "native-pptx.json"

        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(f"原文件：{metadata['original_filename']}")
        print(f"幻灯片页数：{result['page_count']}")

        for name, value in result["summary"].items():
            print(f"{name}: {value}")

        print(f"解析结果：{output_path}")
        print("仅完成原生内容检查，数据库状态仍保持 uploaded")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检查已上传 PPTX 的原生内容")
    parser.add_argument("--document-id", required=True, type=UUID)
    arguments = parser.parse_args()

    asyncio.run(inspect_document(arguments.document_id))
