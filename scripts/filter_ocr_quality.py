import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

POLICY_VERSION = "ocr-quality-v1"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()

    # 去除空格、项目符号和常见排版标点。
    # 保留字母、数字和数学符号，避免直接抹掉公式差异。
    return "".join(
        char
        for char in text
        if not char.isspace()
        and char not in "•●○◦▪■©"
        and not unicodedata.category(char).startswith("P")
    )


def number_groups(text: str) -> list[str]:
    # 数值不同的两行，不能仅因文字相似就当成重复。
    text = unicodedata.normalize("NFKC", text)
    return re.findall(r"\d+(?:\.\d+)?", text)


def native_candidates(blocks: list[dict]) -> list[str]:
    candidates = []

    for block in blocks:
        if block.get("source") != "native":
            continue

        # 隐藏备注不能作为排除屏幕上 OCR 文字的依据。
        if block.get("kind") == "speaker_notes":
            continue

        text = block["text"]
        candidates.append(text)
        candidates.extend(text.splitlines())

        if block.get("kind") == "table":
            for row in block.get("rows", []):
                candidates.extend(row)
                candidates.append(" ".join(row))

    return [text for text in candidates if normalize_text(text)]


def duplicate_match(
    ocr_text: str,
    candidates: list[str],
) -> dict | None:
    normalized = normalize_text(ocr_text)

    if not normalized:
        return None

    for native_text in candidates:
        native_normalized = normalize_text(native_text)

        if normalized == native_normalized:
            return {
                "method": "normalized_exact",
                "similarity": 1.0,
                "native_text": native_text,
            }

    # 短文字的相似度不稳定，只允许上面的完全匹配。
    if len(normalized) < 12:
        return None

    best = None

    for native_text in candidates:
        native_normalized = normalize_text(native_text)

        if number_groups(ocr_text) != number_groups(native_text):
            continue

        length_ratio = min(len(normalized), len(native_normalized)) / max(
            len(normalized), len(native_normalized)
        )

        if length_ratio < 0.9:
            continue

        score = SequenceMatcher(
            None,
            normalized,
            native_normalized,
            autojunk=False,
        ).ratio()

        if score >= 0.92 and (best is None or score > best["similarity"]):
            best = {
                "method": "similar_text_candidate",
                "similarity": round(score, 4),
                "native_text": native_text,
            }

    return best


def apply_policy(document: dict) -> dict:
    if document.get("stage") != "merged":
        raise ValueError("输入必须是合并后的文档。")

    if document.get("quality_policy"):
        raise ValueError("此文件已经处理过，请使用原始 merged-document.json。")

    statistics = Counter()

    for page in document["pages"]:
        candidates = native_candidates(page["blocks"])

        for block in page["blocks"]:
            if block.get("source") != "ocr":
                continue

            previous = bool(block["include_in_retrieval"])
            block["include_in_retrieval_before_quality"] = previous

            quality = {
                "policy_version": POLICY_VERSION,
                "decision": "keep",
                "reason": "no_automatic_exclusion",
            }

            if not previous:
                quality.update(
                    decision="exclude",
                    reason="excluded_by_previous_merge",
                )
            else:
                match = duplicate_match(block["text"], candidates)

                if match:
                    block["include_in_retrieval"] = False
                    quality.update(
                        decision="exclude",
                        reason="duplicate_of_native",
                        match=match,
                    )
                else:
                    words = block.get("words", [])
                    valid_words = [
                        word
                        for word in words
                        if word.get("text", "").strip() and float(word.get("confidence", -1)) >= 0
                    ]

                    if valid_words:
                        total_weight = sum(len(word["text"]) for word in valid_words)
                        mean_confidence = (
                            sum(
                                float(word["confidence"]) * len(word["text"])
                                for word in valid_words
                            )
                            / total_weight
                        )

                        low_weight = sum(
                            len(word["text"])
                            for word in valid_words
                            if float(word["confidence"]) < 60
                        )
                        low_ratio = low_weight / total_weight

                        quality.update(
                            weighted_confidence=round(mean_confidence, 2),
                            low_confidence_char_ratio=round(low_ratio, 4),
                        )

                        # 两个条件同时满足才暂缓使用。
                        if mean_confidence < 45 and low_ratio >= 0.5:
                            block["include_in_retrieval"] = False
                            block["needs_review"] = True
                            quality.update(
                                decision="defer",
                                reason="low_ocr_confidence",
                            )
                    else:
                        # 没有置信度时，不假定它一定错误。
                        block["needs_review"] = True
                        quality["reason"] = "missing_confidence"

            block["quality"] = quality
            statistics[quality["reason"]] += 1

    document["quality_policy"] = {
        "version": POLICY_VERSION,
        "fuzzy_duplicate_threshold": 0.92,
        "minimum_fuzzy_match_characters": 12,
        "defer_mean_confidence_below": 45,
        "defer_low_confidence_ratio_at_least": 0.5,
        "statistics": dict(statistics),
    }
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="标记 OCR 重复与低置信度文字，生成检索输入。")
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()

    source = args.input.expanduser().resolve()

    if not source.is_file():
        parser.error(f"找不到文件：{source}")

    raw = source.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    result = apply_policy(document)

    result["quality_policy"]["input_sha256"] = hashlib.sha256(raw).hexdigest()

    output = source.parent / "quality-reviewed-document.json"
    temporary = output.with_suffix(".json.tmp")

    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output)

    print(
        json.dumps(
            result["quality_policy"],
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"\n输出文件：{output}")


if __name__ == "__main__":
    main()
