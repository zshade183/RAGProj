"""Resume-safe import of a local corpus through the real upload API."""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def list_documents(client: httpx.Client, base_url: str, knowledge_base_id: UUID) -> list[dict]:
    documents, offset = [], 0
    while True:
        response = client.get(
            f"{base_url.rstrip('/')}/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            params={"offset": offset, "limit": 100},
        )
        response.raise_for_status()
        rows = response.json()
        documents.extend(rows)
        if len(rows) < 100:
            return documents
        offset += len(rows)


def wait_for_terminal(
    client: httpx.Client,
    base_url: str,
    knowledge_base_id: UUID,
    local_hashes: set[str],
    timeout: int,
) -> tuple[list[dict], bool]:
    deadline = time.monotonic() + timeout
    while True:
        rows = list_documents(client, base_url, knowledge_base_id)
        selected = [row for row in rows if row["sha256"] in local_hashes]
        pending = [
            row for row in selected if row["status"] not in {"ready", "failed", "deleting"}
        ]
        if not pending:
            return selected, True
        if time.monotonic() >= deadline:
            return selected, False
        time.sleep(5)


def main() -> None:
    parser = argparse.ArgumentParser(description="幂等导入 Markdown 语料并保留断点报告")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个文件；0 表示不限")
    parser.add_argument("--dry-run", action="store_true", help="只计算摘要和预计导入数，不上传")
    parser.add_argument("--wait", action="store_true", help="上传后等待当前语料进入终态")
    parser.add_argument("--wait-timeout", type=int, default=7200)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    files = sorted(args.folder.rglob("*.md"))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        raise ValueError("目录中没有 Markdown 文件")

    unique_files: dict[str, Path] = {}
    folder_duplicates = []
    for path in files:
        sha256 = file_sha256(path)
        if sha256 in unique_files:
            folder_duplicates.append(
                {
                    "skipped": path.relative_to(args.folder).as_posix(),
                    "duplicate_of": unique_files[sha256].relative_to(args.folder).as_posix(),
                }
            )
        else:
            unique_files[sha256] = path

    root = args.base_url.rstrip("/") + (
        f"/api/v1/knowledge-bases/{args.knowledge_base_id}/documents"
    )
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "folder": str(args.folder.resolve()),
        "knowledge_base_id": str(args.knowledge_base_id),
        "source_files": len(files),
        "unique_contents": len(unique_files),
        "folder_duplicates": folder_duplicates,
        "already_present": [],
        "uploaded": [],
        "errors": [],
        "wait_completed": None,
        "final_status_counts": {},
    }
    with httpx.Client(timeout=180) as client:
        existing = {
            row["sha256"]: row
            for row in list_documents(client, args.base_url, args.knowledge_base_id)
            if row["status"] != "deleting"
        }
        if args.dry_run:
            todo = [path for sha, path in unique_files.items() if sha not in existing]
            print(
                f"源文件 {len(files)}，唯一内容 {len(unique_files)}，"
                f"已存在 {len(unique_files) - len(todo)}，待上传 {len(todo)}"
            )
            for path in todo:
                print("WOULD_UPLOAD", path.relative_to(args.folder).as_posix())
            return

        for sha256, path in unique_files.items():
            prior = existing.get(sha256)
            if prior is not None:
                report["already_present"].append(
                    {"path": path.relative_to(args.folder).as_posix(), "document_id": prior["id"]}
                )
                continue
            try:
                with path.open("rb") as stream:
                    response = client.post(
                        root,
                        files={"file": (path.name, stream, "text/markdown")},
                    )
                response.raise_for_status()
                row = response.json()
                existing[sha256] = row
                report["uploaded"].append(
                    {
                        "path": path.relative_to(args.folder).as_posix(),
                        "document_id": row["id"],
                        "status": row["status"],
                    }
                )
                print("UPLOADED", path.name, row["id"], flush=True)
            except (httpx.HTTPError, ValueError) as exc:
                report["errors"].append(
                    {
                        "path": path.relative_to(args.folder).as_posix(),
                        "error": type(exc).__name__,
                    }
                )
                print("ERROR", path.name, type(exc).__name__, flush=True)

        if args.wait and report["uploaded"]:
            selected, completed = wait_for_terminal(
                client,
                args.base_url,
                args.knowledge_base_id,
                set(unique_files),
                args.wait_timeout,
            )
            report["wait_completed"] = completed
            for row in selected:
                status = row["status"]
                report["final_status_counts"][status] = (
                    report["final_status_counts"].get(status, 0) + 1
                )

    report_path = args.report or (
        Path("storage/import-reports")
        / f"corpus-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"已有 {len(report['already_present'])}，新上传 {len(report['uploaded'])}，"
        f"失败 {len(report['errors'])}；报告：{report_path}"
    )
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
