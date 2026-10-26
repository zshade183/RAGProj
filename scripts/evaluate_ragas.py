"""Optional RAGAS scoring of actual evaluate_rag.py responses (never invented outputs)."""

import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from dotenv import dotenv_values
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextRecall, ResponseRelevancy
    from ragas.run_config import RunConfig

    records = json.loads(args.input.read_text(encoding="utf-8"))
    rows = []
    for record in records:
        if "error" in record:
            raise ValueError("回归结果含请求失败，请先修复失败；不静默删除失败样本")
        case, response = record["case"], record["response"]
        if not case.get("reference"):
            raise ValueError("每个样本需要人工核对的 reference 标准答案")
        rows.append(
            {
                "user_input": case["question"],
                "response": response["answer"],
                "retrieved_contexts": [s["text"] for s in response["sources"]],
                "reference": case["reference"],
            }
        )
    if not rows:
        raise ValueError("评估集为空")
    root = Path(__file__).resolve().parents[1]
    config = {**dotenv_values(root / ".env"), **os.environ}
    required = ["RAG_LLM_MODEL", "RAG_LLM_BASE_URL", "RAG_LLM_API_KEY"]
    if any(not config.get(k) for k in required):
        raise ValueError("缺少评估模型配置")
    llm = LangchainLLMWrapper(
        ChatOpenAI(
            model=config["RAG_LLM_MODEL"],
            base_url=config["RAG_LLM_BASE_URL"],
            api_key=config["RAG_LLM_API_KEY"],
            temperature=0,
            timeout=180,
        )
    )
    metrics = [Faithfulness(), LLMContextRecall()]
    embeddings = None
    # Relevancy needs an embedding service; DeepSeek's chat endpoint is not an embedding API.
    if config.get("EVAL_EMBEDDING_MODEL"):
        if not config.get("EVAL_EMBEDDING_BASE_URL") or not config.get("EVAL_EMBEDDING_API_KEY"):
            raise ValueError("请完整配置 EVAL_EMBEDDING_MODEL/BASE_URL/API_KEY")
        embeddings = LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(
                model=config["EVAL_EMBEDDING_MODEL"],
                base_url=config["EVAL_EMBEDDING_BASE_URL"],
                api_key=config["EVAL_EMBEDDING_API_KEY"],
            )
        )
        metrics.append(ResponseRelevancy())
    result = evaluate(
        EvaluationDataset.from_list(rows),
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        run_config=RunConfig(max_workers=1, timeout=180, max_retries=1),
        raise_exceptions=True,
    )
    frame = result.to_pandas()
    metric_columns = [m.name for m in metrics]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False, encoding="utf-8-sig")
    if any(c not in frame or frame[c].isna().any() for c in metric_columns):
        raise RuntimeError("评估返回缺失分数，已保存原始结果，请检查，不能视为通过")
    print(frame[metric_columns].mean().to_string())
    print("这些是模型评估分数，不等同于人工核验准确率。")


if __name__ == "__main__":
    main()
