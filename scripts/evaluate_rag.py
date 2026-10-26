"""Real API regression run. Scores are evidence coverage, NOT fabricated RAGAS accuracy."""

import argparse
import json
import time
from pathlib import Path
from uuid import UUID

import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--knowledge-base-id", type=UUID, required=True)
    p.add_argument("--document-id", type=UUID)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--mode",
        choices=["classic", "agent", "adaptive", "graph1", "graph2"],
        default="adaptive",
    )
    args = p.parse_args()
    cases = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    url = f"{args.base_url.rstrip('/')}/api/v1/knowledge-bases/{args.knowledge_base_id}"
    if args.document_id:
        url += f"/documents/{args.document_id}"
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=1800) as client:
        for case in cases:
            start = time.monotonic()
            try:
                mode = case.get("mode", args.mode)
                if mode not in {"classic", "agent", "adaptive", "graph1", "graph2"}:
                    raise ValueError("样本 mode 无效")
                response = client.post(
                    url + "/ask",
                    json={
                        "question": case["question"],
                        "mode": mode,
                        "allow_web": False,
                        "candidate_k": case.get("candidate_k", 20),
                        "top_k": case.get("top_k", 5),
                        "min_score": case.get("min_score", 0.3),
                    },
                )
                if response.is_error:
                    try:
                        detail = response.json().get("detail", "")
                    except ValueError:
                        detail = response.text[:500]
                    raise RuntimeError(f"HTTP {response.status_code}: {detail}")
                result = response.json()
                expected = case.get("expected_pages", [])
                expected_terms = case.get(
                    "expected_answer_terms",
                    case.get("expected_terms", []),
                )
                cited = [s for s in result["sources"] if s["source_id"] in result["citation_ids"]]
                found = {s["page_number"] for s in cited}
                cited_files = {s["filename"] for s in cited}
                trace_nodes = {item["node"] for item in result.get("trace", [])}
                checks = {
                    "status": (
                        result["status"] == case["expect_status"]
                        if case.get("expect_status")
                        else None
                    ),
                    "expected_page_coverage": (
                        len(set(expected) & found) / len(set(expected)) if expected else None
                    ),
                    "expected_terms_present": all(
                        term in result["answer"] for term in expected_terms
                    ),
                    "forbidden_terms_absent": not any(
                        term in result["answer"] for term in case.get("forbidden_answer_terms", [])
                    ),
                    "minimum_citation_files": (
                        len(cited_files) >= case.get("min_citation_files", 0)
                    ),
                    "required_trace_nodes": set(case.get("required_trace_nodes", [])).issubset(
                        trace_nodes
                    ),
                }
                results.append(
                    {
                        "case": case,
                        "seconds": time.monotonic() - start,
                        "normalized_checks": checks,
                        "all_declared_checks_passed": all(
                            value is True for value in checks.values() if value is not None
                        ),
                        "expected_terms_present": checks["expected_terms_present"],
                        "response": result,
                    }
                )
            except (httpx.HTTPError, KeyError, RuntimeError, TypeError, ValueError) as exc:
                results.append(
                    {
                        "case": case,
                        "error": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    }
                )
            args.output.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print(f"完成 {len(results)} 例；错误 {sum('error' in r for r in results)}；结果：{args.output}")
    print("页码覆盖和关键词命中仅作回归线索，答案与引用正确性仍需人工审核。")


if __name__ == "__main__":
    main()
