# 50 题 RAGAS 评测报告

日期：2026 年 10 月 29 日。

## 结论

在固定的 100 篇文档、634 个文本块上，本次完成了 50 道题的真实检索、回答生成和 RAGAS 评分。每道题使用 5 个检索上下文，150 个指标任务全部完成，没有缺失分数。

| 指标 | 均值 | 中位数 | 低于 0.8 | 满分题数 |
|---|---:|---:|---:|---:|
| Faithfulness | 0.9064 | 1.0000 | 9/50 | 39/50 |
| Context Recall | 0.9000 | 1.0000 | 5/50 | 45/50 |
| Response Relevancy | 0.7823 | 0.9180 | 11/50 | 1/50 |

回答生成平均耗时为 1.264 秒，最短 0.547 秒，最长 2.453 秒。该耗时包含本地检索和回答生成，不包含 RAGAS 评审时间。

结果表明，当前固定 Top-5 稠密检索链路在多数题目上能覆盖标准答案，回答的事实支撑程度较高；回答相关性均值明显低于另外两项，主要受检索不到完整证据、回答保守拒答以及材料之间存在冲突影响。

## 数据集构造

从 100 篇去重 Markdown 中均匀选取候选文档，每篇最多生成 1 道题，最终 50 道题来自 50 个不同文件。生成过程要求：

1. 问题无需查看原文即可理解。
2. 标准答案只能依据当前文件。
3. 必须返回一段逐字摘录的证据。
4. 程序检查证据片段确实存在于原文。
5. 再调用评审模型判断标准答案是否完全由证据支持。

共有 3 个候选未通过校验并被拒绝，随后从剩余文档补足至 50 道。拒绝原因单独保存在 `evaluation/datasets/ragas-50-100docs.rejected.json`。

这些题目属于基于原文的模型辅助标注，并经过逐字证据检查和第二次模型一致性校验，但没有经过人工逐题审核。因此报告不能将其称为 50 道人工标注题。

## 检索与生成设置

- 语料：100 篇 Markdown，634 个文本块。
- 检索模型：`BAAI/bge-small-zh-v1.5`，512 维归一化向量。
- 检索方式：稠密向量 Top-5。
- 生成模型：项目 `.env` 中配置的聊天模型，温度为 0。
- 生成约束：仅依据检索材料回答，材料不足时明确拒答。
- RAGAS：0.4.3。
- Response Relevancy embedding：本地 BGE-small。
- Response Relevancy strictness：1，用于兼容仅支持 `n=1` 的聊天接口。

这次实验没有运行应用中的 BM25、RRF、BGE reranker 和 LangGraph 自适应纠错，因此结果只代表固定 BGE-small Top-5 检索与受约束生成链路。

## 失败样例分析

Context Recall 有 5 道题得 0，集中在应变硅、ALD 和等离子体刻蚀。这些题的标准答案由原始来源文件支持，但全库 Top-5 检索没有稳定找回完整证据，说明相似主题文档之间存在召回竞争。

Faithfulness 有 1 道题得 0、8 道题低于 0.8。典型问题包括回答补充了检索材料没有直接支持的数值或机制，以及多个相似来源出现口径差异。

Response Relevancy 有 7 道题得 0。检查原始回答后发现，其中多道回答明确表示资料或证据不足；这符合生成约束，却不能满足原问题，因此相关性得分较低。50 道回答中共有 8 道包含资料不足、证据不足或无法回答的表述。

低分题不是从均值中删除的异常值，而是后续验证混合检索、重排序和自适应查询改写是否有效的固定回归样例。

## 说服力与限制

相比 5 题先导实验，50 题覆盖 50 个不同来源文件，单题对均值的影响从 20% 降至 2%，结果更适合用于项目说明。它能够证明评测链路已实际运行，并揭示具体检索失败类型。

仍需保留以下限制：

- 标准答案是模型辅助标注，不是人工金标准。
- 题目均来自可回答文档，没有单独设计无答案题和拒答率指标。
- 只验证了稠密 Top-5 路径，没有与 Dense + BM25 + RRF + rerank 做同题对照。
- RAGAS 分数受评审模型、提示词、embedding 模型和版本影响，不等同于人工回答准确率。
- Response Relevancy 使用 strictness=1，不能直接与采用默认多问题生成设置的公开结果比较。

## 简历表述

建议写为：

> 基于 100 篇文档、634 个文本块构建 50 题可复核 RAGAS 评测集，完成真实检索与回答生成；Faithfulness、Context Recall、Response Relevancy 均值分别为 0.906、0.900、0.782，并基于低分题定位应变硅、ALD 等相似主题下的证据召回不足问题。

面试时应补充说明：题目采用模型辅助生成，通过逐字证据和第二模型校验；这些分数是 RAGAS 指标，不是人工准确率。

## 产物

- 50 题数据集：`evaluation/datasets/ragas-50-100docs.jsonl`
- 被拒绝候选：`evaluation/datasets/ragas-50-100docs.rejected.json`
- 检索上下文与回答：`evaluation/results/run-20261029/ragas-50-responses.json`
- 逐题评分：`evaluation/results/run-20261029/ragas-50-scores.csv`
- 机器可读汇总：`evaluation/results/run-20261029/ragas-50-summary.json`
- 题目生成日志：`evaluation/results/run-20261029/ragas-50-generation.log`
- RAGAS 日志：`evaluation/results/run-20261029/ragas-50.log`

## 复现命令

```powershell
python -m scripts.generate_ragas_dataset `
  --folder evaluation/results/run-20261029/corpus100 `
  --output evaluation/datasets/ragas-50-100docs.jsonl `
  --count 50

$env:HF_HUB_OFFLINE='1'
python -m scripts.build_ragas_pilot `
  --folder evaluation/results/run-20261029/corpus100 `
  --dataset evaluation/datasets/ragas-50-100docs.jsonl `
  --vectors evaluation/results/run-20261029/bge-small-100.vectors.npy `
  --output evaluation/results/run-20261029/ragas-50-responses.json `
  --top-k 5

$env:EVAL_LOCAL_EMBEDDING_MODEL='BAAI/bge-small-zh-v1.5'
C:\tools\anaconda3\envs\rag-eval\python.exe scripts/evaluate_ragas.py `
  --input evaluation/results/run-20261029/ragas-50-responses.json `
  --output evaluation/results/run-20261029/ragas-50-scores.csv
```
