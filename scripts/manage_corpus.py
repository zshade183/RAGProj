"""Dry-run-first duplicate cleanup and failed-document retry through the API."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx

STATUS_PRIORITY = {
    "ready": 0,
    "indexing": 1,
    "parsed": 2,
    "parsing": 3,
    "uploaded": 4,
    "failed": 5,
    "uploading": 6,
    "deleting": 7,
}


def list_documents(client: httpx.Client, base_url: str, knowledge_base_id: UUID) -> list[dict]:
    result, offset = [], 0
    while True:
        response = client.get(
            f"{base_url.rstrip('/')}/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            params={"offset": offset, "limit": 100},
        )
        response.raise_for_status()
        page = response.json()
        result.extend(page)
        if len(page) < 100:
            return result
        offset += len(page)


def duplicate_candidates(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in rows:
        if row["status"] != "deleting":
            groups.setdefault(row["sha256"], []).append(row)
    actions = []
    for sha, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(
            key=lambda row: (
                STATUS_PRIORITY.get(row["status"], 99),
                row["created_at"],
                row["id"],
            )
        )
        actions.append(
            {
                "sha256": sha,
                "keep": items[0]["id"],
                "delete": [row["id"] for row in items[1:]],
                "filenames": sorted({row["original_filename"] for row in items}),
            }
        )
    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="核对重复上传并重试失败文档")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--apply", action="store_true", help="实际执行；默认只报告")
    parser.add_argument("--dedupe", action="store_true", help="保留优先级最高的一份并删除其余")
    parser.add_argument("--retry-failed", action="store_true", help="重新排队终态失败文档")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.dedupe and not args.retry_failed:
        parser.error("至少指定 --dedupe 或 --retry-failed")

    base = args.base_url.rstrip("/")
    root = f"{base}/api/v1/knowledge-bases/{args.knowledge_base_id}/documents"
    report = {"deleted": [], "delete_errors": [], "retried": [], "retry_errors": []}
    with httpx.Client(timeout=300) as client:
        rows = list_documents(client, base, args.knowledge_base_id)
        actions = duplicate_candidates(rows)
        if args.dedupe:
            print(f"重复内容组 {len(actions)}，建议删除 {sum(len(a['delete']) for a in actions)} 份")
            if args.apply:
                for action in actions:
                    for document_id in action["delete"]:
                        response = client.delete(f"{root}/{document_id}")
                        if response.is_error:
                            report["delete_errors"].append(
                                {
                                    "document_id": document_id,
                                    "status": response.status_code,
                                    "detail": response.text[:500],
                                }
                            )
                        else:
                            report["deleted"].append(document_id)
        if args.retry_failed:
            failed = [row for row in rows if row["status"] == "failed"]
            print(f"终态失败文档 {len(failed)}")
            if args.apply:
                for row in failed:
                    response = client.post(f"{root}/{row['id']}/process")
                    if response.is_error:
                        report["retry_errors"].append(
                            {
                                "document_id": row["id"],
                                "status": response.status_code,
                                "detail": response.text[:500],
                            }
                        )
                    else:
                        report["retried"].append(row["id"])

    report_path = args.report or (
        Path("storage/import-reports")
        / f"manage-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"apply={args.apply} 删除成功 {len(report['deleted'])}，"
        f"删除失败 {len(report['delete_errors'])}，重试 {len(report['retried'])}；{report_path}"
    )


if __name__ == "__main__":
    main()
