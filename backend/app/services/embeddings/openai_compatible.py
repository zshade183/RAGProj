"""Minimal OpenAI-compatible embeddings client used only for semantic breakpoints."""

import time

import httpx
import numpy as np
from langchain_core.embeddings import Embeddings


class OpenAICompatibleEmbeddings(Embeddings):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        batch_size: int = 64,
        timeout: float = 60,
    ) -> None:
        if not api_key.strip():
            raise ValueError("启用 OpenAI 语义分块时必须配置 OPENAI_EMBEDDING_API_KEY")
        if not model.strip():
            raise ValueError("OPENAI_EMBEDDING_MODEL 不能为空")
        if batch_size < 1:
            raise ValueError("embedding batch_size 必须大于 0")
        endpoint = base_url.rstrip("/")
        self.endpoint = endpoint if endpoint.endswith("/embeddings") else endpoint + "/embeddings"
        self.model = model
        self.batch_size = batch_size
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self.client.close()

    def _request(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "encoding_format": "float",
        }
        last_error = None
        for attempt in range(3):
            try:
                response = self.client.post(self.endpoint, json=payload)
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = RuntimeError(
                        f"Embedding 服务暂时不可用，HTTP {response.status_code}"
                    )
                    time.sleep(0.5 * (2**attempt))
                    continue
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Embedding 服务拒绝请求，HTTP {response.status_code}"
                    )
                try:
                    data = response.json()["data"]
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("Embedding 服务返回格式不正确") from exc
                ordered = sorted(data, key=lambda item: item["index"])
                if len(ordered) != len(texts):
                    raise RuntimeError("Embedding 返回数量与输入不一致")
                vectors = np.asarray([item["embedding"] for item in ordered], dtype=np.float32)
                if vectors.ndim != 2 or not np.isfinite(vectors).all():
                    raise RuntimeError("Embedding 返回了无效向量")
                return vectors.tolist()
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == 2:
                    break
                time.sleep(0.5 * (2**attempt))
        raise RuntimeError("无法连接 Embedding 服务") from last_error

    def embed_documents(self, texts):
        output = []
        for start in range(0, len(texts), self.batch_size):
            output.extend(self._request(texts[start : start + self.batch_size]))
        return output

    def embed_query(self, text):
        return self._request([text])[0]
