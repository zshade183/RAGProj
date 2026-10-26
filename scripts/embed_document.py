import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
from app.services.documents.chunking import (
    CHUNKER_VERSION,
    build_chunks,
)
from app.services.embeddings.bge import (
    MODEL_NAME,
    QUERY_INSTRUCTION,
    BgeEmbedding,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="生成课件向量，并交互式验证语义检索。")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--knowledge-base-id", type=UUID, required=True)
    parser.add_argument("--document-id", type=UUID, required=True)
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
    )
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()

    if args.batch_size <= 0:
        parser.error("--batch-size 必须大于 0。")

    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        parser.error(f"找不到输入文件：{input_path}")

    raw = input_path.read_bytes()
    document = json.loads(raw.decode("utf-8"))

    if document.get("stage") != "merged":
        parser.error("输入必须是 merged-document.json。")

    cache_dir = PROJECT_ROOT / "storage" / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print("正在加载 Embedding 模型；首次运行需要下载模型。", flush=True)
    embedder = BgeEmbedding(
        cache_dir=cache_dir,
        device=args.device,
    )

    print(f"运行设备：{embedder.device}")
    print(f"向量维度：{embedder.dimension}")
    print(f"最大输入长度：{embedder.max_tokens} tokens")

    # 复用已有切块模块，并使用真实 tokenizer 验证长度。
    # 若存在超长块，缩小字符预算后重新生成，不截断正文。
    chunk_size = 400

    while True:
        overlap = min(60, chunk_size // 4)

        chunks = build_chunks(
            document=document,
            knowledge_base_id=args.knowledge_base_id,
            document_id=args.document_id,
            chunk_size=chunk_size,
            overlap=overlap,
        )

        token_counts = [embedder.token_count(chunk["text"]) for chunk in chunks]

        longest = max(token_counts)

        if longest <= embedder.max_tokens:
            break

        if chunk_size <= 32:
            raise RuntimeError("缩小切块后仍超过模型长度限制，请检查文本和 tokenizer。")

        print(
            f"发现 {longest} tokens 的文本块，缩小切块后重试。",
            flush=True,
        )
        chunk_size = max(32, chunk_size // 2)

    for chunk, count in zip(chunks, token_counts, strict=True):
        chunk["token_count"] = count

    print(f"最终切块大小：{chunk_size} 字符")
    print(f"重叠大小：{overlap} 字符")
    print(f"文本块数量：{len(chunks)}")
    print(f"最长文本块：{longest} tokens")

    vectors = embedder.encode_documents(
        [chunk["text"] for chunk in chunks],
        batch_size=args.batch_size,
    )

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = input_path.parent / f"embeddings-{run_id}"
    output_dir.mkdir()

    vector_path = output_dir / "vectors.npy"
    np.save(vector_path, vectors, allow_pickle=False)

    result = {
        "schema_version": 1,
        "stage": "embedded",
        "model_name": MODEL_NAME,
        "query_instruction": QUERY_INSTRUCTION,
        "dimension": embedder.dimension,
        "normalized": True,
        "knowledge_base_id": str(args.knowledge_base_id),
        "document_id": str(args.document_id),
        "source_sha256": document["source_sha256"],
        "merged_document_sha256": hashlib.sha256(raw).hexdigest(),
        "chunker_version": CHUNKER_VERSION,
        "chunk_size_chars": chunk_size,
        "overlap_chars": overlap,
        "chunk_count": len(chunks),
        "vector_file": vector_path.name,
        "vector_file_sha256": hashlib.sha256(vector_path.read_bytes()).hexdigest(),
        "chunks": chunks,
    }

    # 最后写清单：没有清单的目录不视为完整处理结果。
    (output_dir / "embedding-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print()
    print(f"向量已保存：{output_dir}")
    print("输入问题开始检索；输入 exit 退出。")

    while True:
        try:
            question = input("\n问题：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if question.lower() == "exit":
            break

        if not question:
            continue

        try:
            query_vector = embedder.encode_query(question)
        except ValueError as exc:
            print(f"输入有误：{exc}")
            continue

        scores = vectors @ query_vector
        top_indices = np.argsort(-scores, kind="stable")[:5]

        for rank, index in enumerate(top_indices, start=1):
            chunk = chunks[int(index)]

            print(f"\n[{rank}] 第 {chunk['page_number']} 页 | 相似度 {float(scores[index]):.4f}")
            print(f"文本块 ID：{chunk['chunk_id']}")
            print(chunk["text"])


if __name__ == "__main__":
    main()
