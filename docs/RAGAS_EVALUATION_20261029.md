# 100 文档 RAGAS 试验报告

日期：2026 年 10 月 29 日。

## 试验目标

在 100 篇去重 Markdown 文档的固定语料上，验证以下三项回答质量指标：

- Faithfulness：回答中的事实陈述是否能够由检索上下文支持。
- Context Recall：检索上下文是否覆盖人工标准答案所需的信息。
- Response Relevancy：回答是否切合用户问题。

这些指标是 RAGAS 的模型评审和 embedding 评分，不等同于人工核验的回答准确率。

## 试验设计

本次为小规模先导试验，共 5 道题，覆盖 EUV 参数、EUV 产能、TSV 垂直互连、虚拟制造和 EUV/TSV 跨主题比较。问题与标准答案来自项目已有回归数据，并保存为 `evaluation/datasets/ragas-pilot-100docs.jsonl`。

检索语料沿用 100 文档实验的固定清单，共 634 个文本块。检索使用 `BAAI/bge-small-zh-v1.5` 的 512 维归一化向量，每题取相似度最高的 5 个文本块。生成模型使用项目 `.env` 中已有的聊天模型配置，温度为 0，并要求仅依据检索材料回答。

RAGAS 版本为 0.4.3。Faithfulness 和 Context Recall 使用同一聊天模型作为评审模型。Response Relevancy 使用本地 BGE-small embedding；由于当前兼容接口仅支持单次生成，Response Relevancy 的 strictness 设置为 1。评分逐题执行，任一指标缺失均视为评测失败。

## 结果

| 问题 | Faithfulness | Context Recall | Response Relevancy |
|---|---:|---:|---:|
| EUV 波长与先进制程 | 0.8571 | 1.0000 | 0.9355 |
| EUV 光源功率与量产产能 | 1.0000 | 0.0000 | 0.9748 |
| TSV 垂直互连 | 1.0000 | 1.0000 | 0.9053 |
| 虚拟制造缩短开发周期 | 1.0000 | 1.0000 | 0.9941 |
| EUV 与 TSV 对比 | 1.0000 | 1.0000 | 0.9279 |
| **5 题均值** | **0.9714** | **0.8000** | **0.9475** |

5 道题均成功生成回答且三项评分均无空值。回答生成平均耗时为 1.872 秒，最短 1.656 秒，最长 2.266 秒；该耗时不包含 RAGAS 评审时间。

## 结果解释

Faithfulness 均值 0.9714，说明本次回答的大多数事实陈述能够从检索材料中得到支持。第一题为 0.8571，表明其回答中至少有一个被评审模型拆出的陈述未被上下文充分支持。

Context Recall 均值 0.8000。EUV 光源功率题得分为 0，其余 4 题为 1。该题检索上下文包含产能、曝光效率及光能损耗等相关材料，但没有完整覆盖标准答案中被评审模型要求的全部要点，或评审模型未判定为直接覆盖。此结果应作为需要检查检索上下文与标准答案一致性的失败样例，不能从均值中删除。

Response Relevancy 均值 0.9475，5 道回答均与问题高度相关。该指标使用本地 BGE-small 和 strictness=1，与使用 OpenAI embedding 或默认多问题生成设置得到的分数不能直接横向比较。

## 限制

- 样本仅 5 道，且集中在半导体主题，结果适合作为链路验证和先导数据，不能代表整个知识库的总体质量。
- 问题来自已有回归集，100 文档选样时优先纳入了相关主题文档，因此不是随机盲测。
- 检索路径为固定 BGE-small Top-5 稠密检索，没有覆盖应用中的 BM25、RRF、重排序和 LangGraph 自适应流程。
- RAGAS 是模型评审，可能受评审模型、提示词、embedding 模型和版本影响。重要结论仍需人工抽查。

## 可用于简历的表述

> 基于 100 篇文档、634 个文本块构建 5 题 RAGAS 先导评测集，完成真实检索与回答生成；Faithfulness、Context Recall、Response Relevancy 均值分别为 0.971、0.800、0.948，并保留逐题上下文、回答和评分用于失败样例分析。

简历中应保留“5 题先导评测”这一范围说明，不应将这些分数表述为回答准确率。

## 复现材料

- 评测集：`evaluation/datasets/ragas-pilot-100docs.jsonl`
- 回答与检索上下文：`evaluation/results/run-20261029/ragas-pilot-responses.json`
- 逐题评分：`evaluation/results/run-20261029/ragas-pilot-scores.csv`
- 评测日志：`evaluation/results/run-20261029/ragas-pilot.log`
- 回答构建脚本：`scripts/build_ragas_pilot.py`
- RAGAS 评分脚本：`scripts/evaluate_ragas.py`

复现命令：

```powershell
$env:HF_HUB_OFFLINE='1'
python -m scripts.build_ragas_pilot `
  --folder evaluation/results/run-20261029/corpus100 `
  --dataset evaluation/datasets/ragas-pilot-100docs.jsonl `
  --vectors evaluation/results/run-20261029/bge-small-100.vectors.npy `
  --output evaluation/results/run-20261029/ragas-pilot-responses.json `
  --top-k 5

$env:EVAL_LOCAL_EMBEDDING_MODEL='BAAI/bge-small-zh-v1.5'
C:\tools\anaconda3\envs\rag-eval\python.exe scripts/evaluate_ragas.py `
  --input evaluation/results/run-20261029/ragas-pilot-responses.json `
  --output evaluation/results/run-20261029/ragas-pilot-scores.csv
```
