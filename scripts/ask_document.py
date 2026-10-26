import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from app.services.embeddings.bge import (
    MODEL_NAME,
    QUERY_INSTRUCTION,
    BgeEmbedding,
)
from app.services.rag.answering import AnsweringService
from app.services.retrieval.milvus_store import MilvusStore
from app.services.retrieval.reranker import BgeReranker
from dotenv import load_dotenv

from scripts.milvus_document import verify_database_document

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="检索课件，并生成带来源引用的 RAG 回答。")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--uri",
        default="http://127.0.0.1:19530",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
    )

    parser.add_argument(
        "--candidate-k",
        type=int,
        default=20,
        help="Milvus 初步召回数量",
    )
    parser.add_argument(
        "--rerank-min-score",
        type=float,
        default=0.3,
        help="重排序保留阈值，需要根据实际测试校准",
    )
    parser.add_argument(
        "--reranker-device",
        choices=["cpu", "cuda"],
        default="cpu",
    )

    args = parser.parse_args()

    if not 1 <= args.top_k <= 10:
        parser.error("--top-k 必须在 1 到 10 之间。")

    if not args.top_k <= args.candidate_k <= 50:
        parser.error("--candidate-k 必须不小于 --top-k，且不超过 50。")

    if not 0 <= args.rerank_min_score <= 1:
        parser.error("--rerank-min-score 必须在 0 到 1 之间。")

    load_dotenv(PROJECT_ROOT / ".env", override=False)

    api_key = os.getenv("RAG_LLM_API_KEY", "").strip()
    base_url = os.getenv(
        "RAG_LLM_BASE_URL",
        "https://api.deepseek.com",
    ).strip()
    model = os.getenv(
        "RAG_LLM_MODEL",
        "deepseek-flash",
    ).strip()

    if not api_key:
        parser.error("请先在 .env 中配置 RAG_LLM_API_KEY。")

    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.is_file():
        parser.error(f"找不到清单：{manifest_path}")

    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode("utf-8"))

    if (
        manifest.get("stage") != "embedded"
        or manifest.get("model_name") != MODEL_NAME
        or manifest.get("dimension") != 1024
        or manifest.get("query_instruction") != QUERY_INSTRUCTION
        or manifest.get("normalized") is not True
    ):
        raise ValueError("清单与当前 Embedding 配置不兼容。")

    chunks = manifest.get("chunks", [])
    if not chunks or len(chunks) != manifest.get("chunk_count"):
        raise ValueError("清单中的文本块数量异常。")

    kb_id = str(UUID(manifest["knowledge_base_id"]))
    doc_id = str(UUID(manifest["document_id"]))
    snapshot_id = hashlib.sha256(raw).hexdigest()

    asyncio.run(verify_database_document(manifest))

    store = MilvusStore(uri=args.uri)
    answering = None

    try:
        store.ensure_collection()

        actual_count = store.count_snapshot(
            kb_id,
            doc_id,
            snapshot_id,
        )

        if actual_count != manifest["chunk_count"]:
            raise RuntimeError(
                f"Milvus 中有 {actual_count} 条记录，"
                f"预期 {manifest['chunk_count']} 条。"
                "请先完成上一轮的 index 入库。"
            )

        embedder = BgeEmbedding(
            cache_dir=PROJECT_ROOT / "storage" / "models",
            device=args.device,
        )

        print("正在加载重排序模型……", flush=True)

        reranker = BgeReranker(
            cache_dir=PROJECT_ROOT / "storage" / "models",
            device=args.reranker_device,
        )

        print(f"重排序运行设备：{args.reranker_device}")

        answering = AnsweringService(
            base_url=base_url,
            api_key=api_key,
            model=model,
        )

        print()
        print(f"知识库：{kb_id}")
        print(f"文档：{chunks[0]['source_filename']}")
        print(f"回答模型：{model}")
        print("每次问题独立检索；输入 exit 退出。")

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

                candidates = store.search(
                    vector=query_vector.tolist(),
                    knowledge_base_id=kb_id,
                    document_id=doc_id,
                    snapshot_id=snapshot_id,
                    limit=args.candidate_k,
                )

                print(
                    f"召回 {len(candidates)} 个候选段落，正在重排序……",
                    flush=True,
                )

                ranked = reranker.rank(
                    question=question,
                    hits=candidates,
                    batch_size=2,
                )

                print("\n候选段落评分：")

                for position, hit in enumerate(ranked, start=1):
                    entity = hit["entity"]

                    print(
                        f"{position:02d}. "
                        f"第 {entity['page_number']} 页"
                        f" | 向量相似度 {hit['distance']:.4f}"
                        f" | 重排序分数 {hit['rerank_score']:.4f}"
                    )

                accepted = [hit for hit in ranked if hit["rerank_score"] >= args.rerank_min_score]

                hits = accepted[: args.top_k]

                if not hits:
                    print(
                        "\n当前检索结果未达到相关性阈值，"
                        "本次不调用回答模型。"
                        "这不代表整份课件一定没有答案；"
                        "可以换一种问法，或检查召回与阈值。"
                    )
                    continue

                print(
                    f"\n保留 {len(hits)} 个段落，正在生成回答……",
                    flush=True,
                )

                result = answering.answer(question, hits)

            except (ValueError, RuntimeError) as exc:
                print(f"本次问答失败：{exc}")
                continue

            print("\n回答：")
            print(result["answer"])

            if result["citation_check"] == "missing":
                print("\n本次回答没有引用知识库来源。若这是知识问答，请检查是否缺少有效证据。")

            cited = set(result["citation_ids"])

            if cited:
                print("\n引用来源：")

                for source in result["sources"]:
                    if source["source_id"] not in cited:
                        continue

                    print(
                        f"[{source['source_id']}] "
                        f"{source['filename']}，"
                        f"第 {source['page_number']} 页"
                    )
                    print(source["text"])
                    print()

    finally:
        if answering is not None:
            answering.close()
        store.close()


if __name__ == "__main__":
    main()
