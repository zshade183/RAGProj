"""Build real answers and retrieved contexts for the 100-document RAGAS pilot."""

import argparse
import json
import os
import time
from pathlib import Path

import httpx
import numpy as np
from dotenv import dotenv_values
from sentence_transformers import SentenceTransformer

from scripts.verify_bge_small import QUERY_INSTRUCTION, load_chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = {**dotenv_values(root / ".env"), **os.environ}
    required = ("RAG_LLM_MODEL", "RAG_LLM_BASE_URL", "RAG_LLM_API_KEY")
    if any(not config.get(key) for key in required):
        raise ValueError("缺少 RAG_LLM 模型配置")

    cases = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines()]
    _, chunks = load_chunks(args.folder.resolve(), __import__("uuid").UUID("27ad4b76-c515-4de0-8e97-051e520a9910"))
    vectors = np.load(args.vectors, allow_pickle=False)
    if vectors.shape[0] != len(chunks):
        raise ValueError("向量数量与重建文本块数量不一致")

    model = SentenceTransformer(
        "BAAI/bge-small-zh-v1.5",
        device="cpu",
        cache_folder=str(root / "storage" / "models"),
        local_files_only=True,
    )
    endpoint = str(config["RAG_LLM_BASE_URL"]).rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    headers = {"Authorization": f"Bearer {config['RAG_LLM_API_KEY']}"}
    records = []
    with httpx.Client(timeout=180) as client:
        for case in cases:
            started = time.monotonic()
            query_vector = model.encode(
                [QUERY_INSTRUCTION + case["question"]], normalize_embeddings=True
            )[0]
            scores = vectors @ query_vector
            indices = np.argsort(scores)[::-1][: args.top_k]
            contexts = [chunks[int(index)]["text"] for index in indices]
            sources = [
                {
                    "filename": chunks[int(index)]["source_filename"],
                    "page_number": chunks[int(index)]["page_number"],
                    "text": chunks[int(index)]["text"],
                    "score": float(scores[int(index)]),
                }
                for index in indices
            ]
            prompt = "\n\n".join(f"[S{i + 1}] {text}" for i, text in enumerate(contexts))
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "model": config["RAG_LLM_MODEL"],
                    "temperature": 0,
                    "messages": [
                        {
                            "role": "system",
                            "content": "仅依据给定资料回答。资料不足时明确说证据不足。不要使用资料外知识。",
                        },
                        {"role": "user", "content": f"资料：\n{prompt}\n\n问题：{case['question']}"},
                    ],
                },
            )
            response.raise_for_status()
            answer = response.json()["choices"][0]["message"]["content"]
            records.append(
                {
                    "case": case,
                    "seconds": round(time.monotonic() - started, 3),
                    "response": {"answer": answer, "sources": sources},
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"完成 {len(records)}/{len(cases)}", flush=True)


if __name__ == "__main__":
    main()
