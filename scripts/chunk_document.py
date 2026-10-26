import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from app.services.documents.chunking import (
    CHUNKER_VERSION,
    build_chunks,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="将合并后的文档切分为带页码和来源的文本块。")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--overlap", type=int, default=80)
    args = parser.parse_args()

    input_path = args.input.expanduser().resolve()

    if not input_path.is_file():
        parser.error(f"找不到合并文档：{input_path}")

    raw = input_path.read_bytes()
    document = json.loads(raw.decode("utf-8"))

    chunks = build_chunks(
        document=document,
        knowledge_base_id=args.knowledge_base_id,
        document_id=args.document_id,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
    )

    result = {
        "schema_version": 1,
        "stage": "chunked",
        "chunker_version": CHUNKER_VERSION,
        "knowledge_base_id": str(args.knowledge_base_id),
        "document_id": str(args.document_id),
        "source_sha256": document["source_sha256"],
        "merged_document_sha256": hashlib.sha256(raw).hexdigest(),
        "chunk_size_chars": args.chunk_size,
        "overlap_chars": args.overlap,
        "chunk_count": len(chunks),
        "chunks": chunks,
    }

    output = input_path.parent / (
        f"chunks-{args.document_id}-{args.chunk_size}-{args.overlap}.json"
    )
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output)

    print(f"文档页数：{document['page_count']}")
    print(f"有文本块的页数：{len({c['page_number'] for c in chunks})}")
    print(f"文本块数量：{len(chunks)}")
    print(f"最长文本块：{max(c['char_count'] for c in chunks)} 字符")
    print(f"包含待检查 OCR 内容的文本块：{sum(c['needs_review'] for c in chunks)}")
    print(f"输出文件：{output}")


if __name__ == "__main__":
    main()
