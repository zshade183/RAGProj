from functools import lru_cache
from pathlib import Path

from minio import Minio
from minio.error import S3Error
from urllib3 import PoolManager, Timeout

from app.core.config import get_settings


class MinioStorage:
    """负责原始文档的对象存储操作。"""

    # 初始化 MinIO 客户端
    # 从配置中获取 MinIO 服务器地址、访问密钥、密钥和是否需要安全连接
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket_name = settings.minio_bucket_documents

        self.client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
            http_client=PoolManager(
                timeout=Timeout(connect=5.0, read=60.0),
                retries=False,
            ),
        )

    def ensure_bucket(self) -> None:
        """桶不存在时创建；已存在时保持原样。"""

        if self.client.bucket_exists(self.bucket_name):
            return

        try:
            self.client.make_bucket(self.bucket_name)
        except S3Error as exc:
            # 另一个初始化进程可能刚好创建了同一个桶。
            if exc.code != "BucketAlreadyOwnedByYou":
                raise

    def upload_file(
        self,
        *,
        object_name: str,
        file_path: Path,
        content_type: str = "application/octet-stream",
    ) -> None:
        """上传本地文件。"""

        self.client.fput_object(
            bucket_name=self.bucket_name,
            object_name=object_name,
            file_path=str(file_path),
            content_type=content_type,
        )

    def download_file(
        self,
        *,
        object_name: str,
        file_path: Path,
        bucket_name: str | None = None,
    ) -> None:
        """从指定桶下载；未指定时使用默认桶。"""

        self.client.fget_object(
            bucket_name=(bucket_name if bucket_name is not None else self.bucket_name),
            object_name=object_name,
            file_path=str(file_path),
        )

    def delete_file(
        self,
        *,
        object_name: str,
        bucket_name: str | None = None,
    ) -> None:
        """删除指定对象；未指定桶时使用默认桶。"""

        self.client.remove_object(
            bucket_name=(bucket_name if bucket_name is not None else self.bucket_name),
            object_name=object_name,
        )

    def delete_prefix(
        self,
        *,
        prefix: str,
        bucket_name: str | None = None,
    ) -> int:
        """删除指定前缀下的全部对象，返回已提交删除请求的对象数。"""

        if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("对象前缀不安全")
        target_bucket = bucket_name if bucket_name is not None else self.bucket_name
        removed = 0
        for item in self.client.list_objects(
            bucket_name=target_bucket,
            prefix=prefix,
            recursive=True,
        ):
            self.client.remove_object(
                bucket_name=target_bucket,
                object_name=item.object_name,
            )
            removed += 1
        return removed

    def list_prefix(
        self,
        *,
        prefix: str,
        bucket_name: str | None = None,
    ) -> list[str]:
        if not prefix or prefix.startswith("/") or ".." in prefix.split("/"):
            raise ValueError("对象前缀不安全")
        target_bucket = bucket_name if bucket_name is not None else self.bucket_name
        return [
            item.object_name
            for item in self.client.list_objects(
                bucket_name=target_bucket,
                prefix=prefix,
                recursive=True,
            )
        ]


@lru_cache
def get_storage() -> MinioStorage:
    """在当前进程中复用存储客户端。"""

    return MinioStorage()
