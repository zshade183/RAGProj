import os
from pathlib import Path

import numpy as np
import torch

if os.getenv("MODEL_LOCAL_FILES_ONLY", "true").lower() in {"1", "true", "yes"}:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from sentence_transformers import SentenceTransformer

from app.services.embeddings.constants import MODEL_NAME, QUERY_INSTRUCTION


class BgeEmbedding:
    def __init__(
        self,
        cache_dir: Path,
        device: str = "auto",
        local_files_only: bool = True,
    ) -> None:
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("当前 PyTorch 无法使用 CUDA。")
        if local_files_only:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        self.device = device

        self.model = SentenceTransformer(
            MODEL_NAME,
            device=device,
            cache_folder=str(cache_dir),
            trust_remote_code=False,
            local_files_only=local_files_only,
            model_kwargs={"local_files_only": local_files_only},
            processor_kwargs={"local_files_only": local_files_only},
        )

        # 本服务明确针对 bge-large-zh-v1.5。
        self.max_tokens = min(
            int(self.model.max_seq_length),
            512,
        )
        self.dimension = self.model.get_embedding_dimension()

        if self.dimension != 1024:
            raise RuntimeError(f"模型维度不符合预期：{self.dimension}")

    def token_count(self, text: str) -> int:
        encoded = self.model.tokenizer(
            text,
            add_special_tokens=True,
            truncation=False,
        )
        return len(encoded["input_ids"])

    def encode_documents(
        self,
        texts: list[str],
        batch_size: int = 4,
    ) -> np.ndarray:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("待向量化的文本不能为空。")

        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0。")

        for index, text in enumerate(texts):
            count = self.token_count(text)
            if count > self.max_tokens:
                raise ValueError(
                    f"第 {index} 个文本包含 {count} tokens，"
                    f"超过上限 {self.max_tokens}，需要重新切块。"
                )

        vectors = self.model.encode(
            texts,
            prompt="",
            batch_size=batch_size,
            show_progress_bar=True,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        vectors = np.asarray(vectors, dtype=np.float32)

        if vectors.shape != (len(texts), self.dimension):
            raise RuntimeError(f"向量形状异常：{vectors.shape}")

        if not np.isfinite(vectors).all():
            raise RuntimeError("向量包含 NaN 或无穷值。")

        if not np.allclose(
            np.linalg.norm(vectors, axis=1),
            1.0,
            atol=1e-4,
        ):
            raise RuntimeError("向量没有正确归一化。")

        return vectors

    def encode_query(self, question: str) -> np.ndarray:
        question = question.strip()
        if not question:
            raise ValueError("问题不能为空。")

        text = QUERY_INSTRUCTION + question

        if self.token_count(text) > self.max_tokens:
            raise ValueError("问题过长，请缩短后重试。")

        vector = self.model.encode(
            [text],
            prompt="",
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        vector = np.asarray(vector[0], dtype=np.float32)

        if vector.shape != (self.dimension,):
            raise RuntimeError("问题向量维度异常。")

        if not np.isfinite(vector).all():
            raise RuntimeError("问题向量包含无效数值。")

        return vector
