"""Build a reproducible source bundle without secrets, caches, models or database dumps."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = {
    ".env.example",
    ".gitignore",
    "README.md",
    "alembic.ini",
    "compose.yaml",
    "compose.unstructured.yaml",
    "pyproject.toml",
    "requirements-before-core.txt",
    "start.md",
}
TOP_DIRS = {"backend", "deploy", "docs", "evaluation", "frontend", "md", "scripts"}
EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "egg-info",
    "storage",
}
EXCLUDED_SUFFIXES = {".pyc", ".npy", ".zip", ".sql", ".pptx", ".pdf"}


def included(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if path.name == ".env" or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if relative.parts[0] in TOP_DIRS:
        return path.is_file()
    return relative.as_posix() in TOP_FILES


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="生成不含密钥与运行数据的源码交付包")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-corpus", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in ROOT.rglob("*") if included(path))
    if args.exclude_corpus:
        files = [path for path in files if path.relative_to(ROOT).parts[0] != "md"]
    if not files:
        raise ValueError("没有可打包文件")

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = "unknown", True

    package_name = "rag-enterprise-knowledge-base"
    with tempfile.TemporaryDirectory(prefix="rag-release-") as temp:
        stage = Path(temp) / package_name
        for source in files:
            relative = source.relative_to(ROOT)
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        manifest = {
            "package": package_name,
            "generated_at": datetime.now(UTC).isoformat(),
            "git_commit": commit,
            "git_dirty": dirty,
            "python": ">=3.11,<3.12",
            "files": [
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": digest(path),
                    "bytes": path.stat().st_size,
                }
                for path in files
            ],
            "excluded": [
                ".env",
                "storage/",
                "模型缓存",
                "数据库备份",
                "Python/测试缓存",
                "原始二进制 PDF/PPTX",
            ],
        }
        (stage / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with zipfile.ZipFile(
            args.output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage.parent).as_posix())

    print(
        json.dumps(
            {
                "output": str(args.output),
                "bytes": args.output.stat().st_size,
                "sha256": digest(args.output),
                "files": len(files) + 1,
                "git_commit": commit,
                "git_dirty": dirty,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
