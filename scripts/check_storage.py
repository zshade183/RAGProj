from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.services.storage.minio_storage import get_storage


def main() -> None:
    # 初始化存储客户端
    storage = get_storage()
    # 确保存储桶存在
    storage.ensure_bucket()
    print(f"[1/4] 存储桶就绪：{storage.bucket_name}")

    object_name = f"checks/{uuid4().hex}.txt"
    content = "企业知识库 MinIO 中文读写验证。\n".encode()

    with TemporaryDirectory(prefix="rag-storage-check-") as temp_dir:
        directory = Path(temp_dir)
        source = directory / "source.txt"
        downloaded = directory / "downloaded.txt"

        source.write_bytes(content)

        storage.upload_file(
            object_name=object_name,
            file_path=source,
            content_type="text/plain; charset=utf-8",
        )

        try:
            print("[2/4] 测试文件上传成功")

            storage.download_file(
                object_name=object_name,
                file_path=downloaded,
            )

            if downloaded.read_bytes() != content:
                raise RuntimeError("下载内容与上传内容不一致")

            print("[3/4] 下载成功，文件内容完全一致")
        finally:
            storage.delete_file(object_name=object_name)
            print("[4/4] 本次测试对象已清理")

    print("MinIO 读写检查通过")


if __name__ == "__main__":
    main()
