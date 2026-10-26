import os
from pathlib import Path

import torch

if os.getenv("MODEL_LOCAL_FILES_ONLY", "true").lower() in {"1", "true", "yes"}:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
)

RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


class BgeReranker:
    def __init__(
        self,
        cache_dir: Path,
        device: str = "cpu",
        max_pair_tokens: int = 1024,
        local_files_only: bool = True,
    ) -> None:
        if device not in {"cpu", "cuda"}:
            raise ValueError("device 必须是 cpu 或 cuda。")

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("当前 PyTorch 无法使用 CUDA。")
        if local_files_only:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        self.device = device
        self.max_pair_tokens = max_pair_tokens

        self.tokenizer = AutoTokenizer.from_pretrained(
            RERANKER_MODEL,
            cache_dir=str(cache_dir),
            trust_remote_code=False,
            local_files_only=local_files_only,
        )

        self.model = AutoModelForSequenceClassification.from_pretrained(
            RERANKER_MODEL,
            cache_dir=str(cache_dir),
            trust_remote_code=False,
            local_files_only=local_files_only,
        )

        if device == "cuda":
            self.model.half()

        self.model.to(device)
        self.model.eval()

    def rank(
        self,
        question: str,
        hits: list[dict],
        batch_size: int = 2,
    ) -> list[dict]:
        if not question.strip():
            raise ValueError("问题不能为空。")

        if batch_size <= 0:
            raise ValueError("batch_size 必须大于 0。")

        if not hits:
            return []

        # 相同 chunk 只评分一次。
        candidates = []
        seen = set()

        for hit in hits:
            chunk_id = hit["entity"]["chunk_id"]

            if chunk_id in seen:
                continue

            seen.add(chunk_id)
            candidates.append(hit)

        pairs = [[question, hit["entity"]["text"]] for hit in candidates]

        # 不静默截断：先检查问题与段落合起来的长度。
        for index, pair in enumerate(pairs):
            encoded = self.tokenizer(
                pair[0],
                pair[1],
                add_special_tokens=True,
                truncation=False,
            )
            token_count = len(encoded["input_ids"])

            if token_count > self.max_pair_tokens:
                raise ValueError(
                    f"候选段落 {index + 1} 与问题合计 "
                    f"{token_count} tokens，超过本服务预算 "
                    f"{self.max_pair_tokens}。"
                    "请缩短问题或调整分块，程序没有截断正文。"
                )

        scores = []

        with torch.inference_mode():
            for start in range(0, len(pairs), batch_size):
                batch = pairs[start : start + batch_size]

                inputs = self.tokenizer(
                    [pair[0] for pair in batch],
                    [pair[1] for pair in batch],
                    padding=True,
                    truncation=False,
                    return_tensors="pt",
                )
                inputs = {name: value.to(self.device) for name, value in inputs.items()}

                logits = self.model(**inputs).logits

                if logits.shape != (len(batch), 1):
                    raise RuntimeError(f"重排序输出形状异常：{logits.shape}")

                batch_scores = torch.sigmoid(logits[:, 0].float())

                if not torch.isfinite(batch_scores).all():
                    raise RuntimeError("重排序分数包含无效数值。")

                scores.extend(batch_scores.cpu().tolist())

        ranked = []

        for hit, score in zip(candidates, scores, strict=True):
            # 保留 Milvus 原来的 distance，不混淆两种分数。
            ranked.append(
                {
                    **hit,
                    "rerank_score": float(score),
                }
            )

        return sorted(
            ranked,
            key=lambda hit: hit["rerank_score"],
            reverse=True,
        )
