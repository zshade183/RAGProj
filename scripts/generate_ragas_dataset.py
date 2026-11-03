"""Generate grounded RAGAS labels from the fixed 100-document corpus."""

import argparse
import json
import os
from pathlib import Path

import httpx
from dotenv import dotenv_values


def call_json(client, endpoint, headers, model, messages):
    response = client.post(
        endpoint,
        headers=headers,
        json={"model": model, "temperature": 0, "response_format": {"type": "json_object"}, "messages": messages},
    )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count 必须大于 0")

    root = Path(__file__).resolve().parents[1]
    config = {**dotenv_values(root / ".env"), **os.environ}
    required = ("RAG_LLM_MODEL", "RAG_LLM_BASE_URL", "RAG_LLM_API_KEY")
    if any(not config.get(key) for key in required):
        raise ValueError("缺少 RAG_LLM 模型配置")
    endpoint = str(config["RAG_LLM_BASE_URL"]).rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    headers = {"Authorization": f"Bearer {config['RAG_LLM_API_KEY']}"}

    files = sorted(args.folder.glob("*.md"))
    usable = [(path, path.read_text(encoding="utf-8").strip()) for path in files]
    usable = [(path, text) for path, text in usable if len(text) >= 300]
    if len(usable) < args.count:
        raise ValueError("可用文档数量不足")

    # Spread selection across the sorted corpus rather than taking one contiguous prefix.
    # Start with an evenly spread order, then use the remaining documents as
    # replacements when a candidate fails evidence validation.
    primary_indices = [(index * len(usable)) // args.count for index in range(args.count)]
    ordered_indices = primary_indices + [index for index in range(len(usable)) if index not in primary_indices]
    selected = [usable[index] for index in ordered_indices]
    rows = []
    rejected = []
    with httpx.Client(timeout=180) as client:
        for candidate_index, (path, text) in enumerate(selected, 1):
            if len(rows) >= args.count:
                break
            evidence = text[:6000]
            item = call_json(
                client,
                endpoint,
                headers,
                config["RAG_LLM_MODEL"],
                [
                    {
                        "role": "system",
                        "content": (
                            "你在为RAG系统创建可复核评测题。只能依据给定原文，输出JSON对象，字段为"
                            "question、reference、evidence_quote。问题必须具体且无需看到原文即可理解；"
                            "标准答案应简洁完整；evidence_quote必须逐字摘自原文且直接支持答案。"
                        ),
                    },
                    {"role": "user", "content": f"文件：{path.name}\n原文：\n{evidence}"},
                ],
            )
            quote = str(item.get("evidence_quote", "")).strip()
            question = str(item.get("question", "")).strip()
            reference = str(item.get("reference", "")).strip()
            if not question or not reference or not quote or quote not in evidence:
                rejected.append({"source_file": path.name, "reason": "逐字证据校验失败"})
                print(f"拒绝候选 {candidate_index}: 逐字证据校验失败", flush=True)
                continue
            validation = call_json(
                client,
                endpoint,
                headers,
                config["RAG_LLM_MODEL"],
                [
                    {
                        "role": "system",
                        "content": "判断标准答案是否完全由证据支持。只输出JSON：supported布尔值、reason字符串。",
                    },
                    {
                        "role": "user",
                        "content": f"问题：{question}\n标准答案：{reference}\n证据：{quote}",
                    },
                ],
            )
            if validation.get("supported") is not True:
                rejected.append({"source_file": path.name, "reason": validation.get("reason", "")})
                print(f"拒绝候选 {candidate_index}: 一致性校验失败", flush=True)
                continue
            rows.append(
                {
                    "question": question,
                    "reference": reference,
                    "source_file": path.name,
                    "evidence_quote": quote,
                    "label_method": "llm_generated_exact_quote_and_llm_validated",
                }
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            print(f"标注 {len(rows)}/{args.count}", flush=True)
    if len(rows) != args.count:
        raise RuntimeError(f"仅生成 {len(rows)} 道合格题，目标为 {args.count}")
    args.output.with_suffix(".rejected.json").write_text(
        json.dumps(rejected, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
