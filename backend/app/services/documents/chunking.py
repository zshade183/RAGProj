import hashlib
import json
from uuid import UUID, uuid5

CHUNKER_VERSION = "page-char-v1"


def structure_payload(block: dict) -> dict:
    return {
        key: block[key]
        for key in ("table", "chart", "relation", "text_as_html", "coordinates")
        if block.get(key) is not None
    }


# 分块函数
def split_ranges(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[tuple[int, int]]:
    """返回原文中的 [start, end) 区间，保留准确来源位置。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0。")

    if not 0 <= overlap < chunk_size // 2:
        raise ValueError("overlap 必须非负且小于 chunk_size 的一半。")

    ranges = []
    start = 0

    while start < len(text):
        limit = min(start + chunk_size, len(text))
        end = limit

        if limit < len(text):
            # 在后半段寻找边界，避免为了凑句子切出过短块。
            lower = start + chunk_size // 2

            # 优先级：段落、换行、句末标点。
            for separators in (
                ("\n\n",),
                ("\n",),
                ("。", "！", "？", "；", ". ", "! ", "? "),
            ):
                candidates = []

                for separator in separators:
                    position = text.rfind(separator, lower, limit)
                    if position != -1:
                        candidates.append(position + len(separator))

                if candidates:
                    end = max(candidates)
                    break

        ranges.append((start, end))

        if end == len(text):
            break

        # 至少前进一个字符，保证循环一定结束。
        start = max(start + 1, end - overlap)

    return ranges


def build_chunks(
    document: dict,
    knowledge_base_id: UUID,
    document_id: UUID,
    chunk_size: int = 600,
    overlap: int = 80,
) -> list[dict]:
    if document.get("stage") != "merged":
        raise ValueError("输入必须是合并完成的文档。")

    pages = document.get("pages", [])

    if document.get("page_count") != len(pages):
        raise ValueError("文档页数与 pages 数量不一致。")

    if [page["page_number"] for page in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("页码必须连续且不能重复。")

    # 即使没有有效文字，也先校验参数。
    split_ranges("", chunk_size, overlap)

    source_hash = document["source_sha256"]
    chunks = []

    for page in pages:
        page_number = page["page_number"]
        parts = []
        spans = []
        cursor = 0

        for block in page["blocks"]:
            if not block["include_in_retrieval"]:
                continue

            text = block["text"].strip()
            if not text:
                continue

            if parts:
                parts.append("\n\n")
                cursor += 2

            start = cursor
            parts.append(text)
            cursor += len(text)

            spans.append(
                {
                    "block_id": block["block_id"],
                    "source": block["source"],
                    "kind": block["kind"],
                    "start": start,
                    "end": cursor,
                    "needs_review": block.get("needs_review", False),
                    "structure": structure_payload(block),
                }
            )

        page_text = "".join(parts)

        for start, end in split_ranges(page_text, chunk_size, overlap):
            # 去掉块两侧的空白，同时修正来源位置。
            while start < end and page_text[start].isspace():
                start += 1

            while end > start and page_text[end - 1].isspace():
                end -= 1

            if start == end:
                continue

            text = page_text[start:end]
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

            source_blocks = []

            for span in spans:
                intersect_start = max(start, span["start"])
                intersect_end = min(end, span["end"])

                if intersect_start >= intersect_end:
                    continue

                source_blocks.append(
                    {
                        "block_id": span["block_id"],
                        "source": span["source"],
                        "kind": span["kind"],
                        # 在原始块经过 strip 后的文字中的位置。
                        "block_char_start": (intersect_start - span["start"]),
                        "block_char_end": (intersect_end - span["start"]),
                        # 在当前文本块中的位置。
                        "chunk_char_start": intersect_start - start,
                        "chunk_char_end": intersect_end - start,
                        "needs_review": span["needs_review"],
                        "structure": span.get("structure", {}),
                    }
                )

            identity = json.dumps(
                {
                    "version": CHUNKER_VERSION,
                    "knowledge_base_id": str(knowledge_base_id),
                    "source_sha256": source_hash,
                    "page_number": page_number,
                    "start": start,
                    "end": end,
                    "content_sha256": content_hash,
                    "chunk_size": chunk_size,
                    "overlap": overlap,
                    "source_blocks": source_blocks,
                },
                ensure_ascii=False,
                sort_keys=True,
            )

            chunks.append(
                {
                    "chunk_id": str(uuid5(document_id, identity)),
                    "knowledge_base_id": str(knowledge_base_id),
                    "document_id": str(document_id),
                    "source_filename": document["source_filename"],
                    "source_sha256": source_hash,
                    "page_number": page_number,
                    "chunk_index": len(chunks),
                    "text": text,
                    "char_count": len(text),
                    "page_char_start": start,
                    "page_char_end": end,
                    "content_sha256": content_hash,
                    "source_blocks": source_blocks,
                    "needs_review": any(item["needs_review"] for item in source_blocks),
                }
            )

    if not chunks:
        raise ValueError("文档没有可用于检索的文字，不能生成文本块。")

    return chunks
