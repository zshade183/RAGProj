import argparse
import csv
import hashlib
import io
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)

    return digest.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_tesseract(
    executable: Path,
    image_path: Path,
    languages: str,
    timeout: int,
) -> subprocess.CompletedProcess:
    command = [
        str(executable),
        str(image_path),
        "stdout",
        "-l",
        languages,
        "--oem",
        "1",
        "--psm",
        "3",
        "tsv",
    ]

    return subprocess.run(
        command,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def parse_tsv(content: str) -> tuple[list[dict], list[dict]]:
    words = []
    grouped_lines = {}

    reader = csv.DictReader(
        io.StringIO(content),
        delimiter="\t",
        quoting=csv.QUOTE_NONE,
    )

    required_fields = {
        "level",
        "block_num",
        "par_num",
        "line_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    }

    if not required_fields.issubset(set(reader.fieldnames or [])):
        raise ValueError("OCR 输出缺少必要的 TSV 字段。")

    for row in reader:
        # level=5 表示文字单元，其他层级不重复收集。
        if row["level"] != "5":
            continue

        text = (row["text"] or "").strip()
        if not text:
            continue

        confidence = float(row["conf"])
        x = int(row["left"])
        y = int(row["top"])
        width = int(row["width"])
        height = int(row["height"])

        line_key = (
            int(row["block_num"]),
            int(row["par_num"]),
            int(row["line_num"]),
        )

        word_index = len(words)

        words.append(
            {
                "text": text,
                "confidence": confidence,
                "bbox_px": [x, y, width, height],
                "line_key": list(line_key),
                "needs_review": confidence < 60,
            }
        )
        grouped_lines.setdefault(line_key, []).append(word_index)

    lines = []

    for line_key, indices in grouped_lines.items():
        line_words = [words[index] for index in indices]

        left = min(word["bbox_px"][0] for word in line_words)
        top = min(word["bbox_px"][1] for word in line_words)
        right = max(word["bbox_px"][0] + word["bbox_px"][2] for word in line_words)
        bottom = max(word["bbox_px"][1] + word["bbox_px"][3] for word in line_words)

        lines.append(
            {
                "line_key": list(line_key),
                "word_indices": indices,
                # 仅用于人工预览。中文分词间空格后续另行规范化。
                "text_preview": " ".join(word["text"] for word in line_words),
                "bbox_px": [
                    left,
                    top,
                    right - left,
                    bottom - top,
                ],
            }
        )

    return words, lines


def main() -> None:
    parser = argparse.ArgumentParser(description="根据页面渲染清单，执行逐页中英文 OCR。")
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="上一阶段生成的 manifest.json",
    )
    parser.add_argument(
        "--tesseract",
        type=Path,
        default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    )
    parser.add_argument(
        "--languages",
        default="chi_sim+eng",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="每页 OCR 的超时时间，单位秒",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    executable = args.tesseract.expanduser().resolve()

    if not manifest_path.is_file():
        parser.error(f"找不到渲染清单：{manifest_path}")

    if not executable.is_file():
        parser.error(f"找不到 Tesseract：{executable}")

    if args.timeout <= 0:
        parser.error("--timeout 必须大于 0。")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if manifest.get("stage") != "rendered":
        parser.error("清单不是上一阶段成功完成的渲染结果。")

    pages = manifest.get("pages", [])

    if not pages or len(pages) != manifest.get("page_count"):
        parser.error("清单页数不一致，或没有可处理的页面。")

    numbers = [page["page_number"] for page in pages]
    if numbers != list(range(1, len(pages) + 1)):
        parser.error("清单中的页码不连续或存在重复。")

    # 提前检查语言包，避免每一页都重复报缺少语言包。
    language_check = subprocess.run(
        [str(executable), "--list-langs"],
        capture_output=True,
        timeout=30,
        check=False,
    )

    if language_check.returncode != 0:
        raise RuntimeError(language_check.stderr.decode("utf-8", errors="replace"))

    installed_languages = {
        line.strip()
        for line in language_check.stdout.decode("utf-8", errors="replace").splitlines()
    }

    missing = set(args.languages.split("+")) - installed_languages
    if missing:
        parser.error(f"缺少语言包：{', '.join(sorted(missing))}")

    version_result = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        timeout=30,
        check=True,
    )
    engine_version = version_result.stdout.decode("utf-8", errors="replace").strip()

    render_dir = manifest_path.parent
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = render_dir / f"ocr-{run_id}"
    output_dir.mkdir()

    page_results = []

    print(f"OCR 输出目录：{output_dir}", flush=True)

    for page in pages:
        page_number = page["page_number"]
        prefix = f"page-{page_number:04d}"

        result_record = {
            "page_number": page_number,
            "status": "failed",
        }

        try:
            image_path = (render_dir / page["image_path"]).resolve()

            if not image_path.is_relative_to(render_dir):
                raise ValueError("图片路径不在渲染目录中。")

            if sha256_file(image_path) != page["sha256"]:
                raise ValueError("图片哈希与清单不一致，请重新渲染。")

            result = run_tesseract(
                executable=executable,
                image_path=image_path,
                languages=args.languages,
                timeout=args.timeout,
            )

            (output_dir / f"{prefix}.stderr.log").write_bytes(result.stderr)

            if result.returncode != 0:
                raise RuntimeError(
                    f"Tesseract 运行失败，退出码：{result.returncode}；请查看 {prefix}.stderr.log"
                )

            (output_dir / f"{prefix}.tsv").write_bytes(result.stdout)

            words, lines = parse_tsv(result.stdout.decode("utf-8-sig"))

            page_data = {
                "page_number": page_number,
                "image_sha256": page["sha256"],
                "width_px": page["width_px"],
                "height_px": page["height_px"],
                "coordinate_system": "image_pixels_top_left",
                "words": words,
                "lines": lines,
                "no_text_detected": not words,
                "low_confidence_count": sum(word["needs_review"] for word in words),
            }

            write_json(
                output_dir / f"{prefix}.json",
                page_data,
            )

            (output_dir / f"{prefix}.txt").write_text(
                "\n".join(line["text_preview"] for line in lines),
                encoding="utf-8",
            )

            result_record.update(
                {
                    "status": "succeeded",
                    "result_file": f"{prefix}.json",
                    "word_count": len(words),
                    "no_text_detected": not words,
                }
            )

            print(
                f"[{page_number}/{len(pages)}] 完成，{len(words)} 个文字单元",
                flush=True,
            )

        except Exception as exc:  # noqa: BLE001 - record per-page failures
            result_record["error"] = f"{type(exc).__name__}: {exc}"
            print(
                f"[{page_number}/{len(pages)}] 失败：{exc}",
                flush=True,
            )

        page_results.append(result_record)

        # 每页结束保存一次进度；中断后也能看到已完成结果。
        write_json(
            output_dir / "progress.json",
            {
                "total_pages": len(pages),
                "processed_pages": len(page_results),
                "pages": page_results,
            },
        )

    failed_count = sum(page["status"] == "failed" for page in page_results)

    summary = {
        "schema_version": 1,
        "stage": "ocr_completed" if failed_count == 0 else "ocr_failed",
        "engine": "tesseract",
        "engine_version": engine_version,
        "languages": args.languages,
        "psm": 3,
        "oem": 1,
        "source_sha256": manifest["source_sha256"],
        "render_manifest_sha256": sha256_file(manifest_path),
        "page_count": len(pages),
        "failed_pages": failed_count,
        "completed_at": datetime.now(UTC).isoformat(),
        "pages": page_results,
    }

    write_json(output_dir / "ocr-manifest.json", summary)

    print()
    print(f"成功：{len(pages) - failed_count} 页")
    print(f"失败：{failed_count} 页")
    print(f"结果目录：{output_dir}")

    if failed_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
