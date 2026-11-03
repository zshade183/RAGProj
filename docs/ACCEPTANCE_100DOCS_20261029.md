# 100 文档实验与现有测试验收记录

日期：2026 年 10 月 29 日。仅记录本次实际执行结果，历史验收记录未覆盖。

## 实验范围与数据

使用现有 scripts/verify_bge_small.py，选取 100 篇真实 Markdown，按 SHA-256 去重。优先纳入 EUV、TSV、虚拟制造相关文档及原有检索样例目标文档，再按文件名排序补足；这是面向既有样例的验证集，不是随机盲测集。可复现源文件与摘要见 evaluation/results/run-20261029/selection.json，文档副本见同目录 corpus100。

采用独立知识库标识和脚本生成的临时 Milvus 集合，未删除原始文档；脚本结束后按原有逻辑清理临时集合，向量文件和结果已保留。这项实验验证 BGE-small 参考检索路径，不代表 BGE-large 混合检索及生成链路的端到端评估。

## 实际结果

| 项目 | 本次结果 |
|---|---|
| 文档数 | 100，内容摘要去重 |
| 文本块数 | 634 |
| 向量模型 | BAAI/bge-small-zh-v1.5，512 维 |
| 长度检查 | 全部块通过脚本的 512 token 上限检查 |
| 向量化、索引及检索总耗时 | 32.36 秒；不是单次检索延迟，也不是问答延迟 |
| 检索样例 | EUV光源功率如何影响7nm以下制程？ |
| 第一条命中文档 | tech_report_cp7b0kn4.md |
| 第一条命中余弦相似度 | 0.805954；不是准确率 |
| 默认 pytest | 45 passed、9 skipped，23.66 秒 |
| 真实服务集成测试 | 重跑后 8 passed：PostgreSQL 7 项、Milvus 1 项，28.92 秒 |
| Ruff 静态检查 | 11 处问题，未通过；原始输出见 ruff.txt |
| AES classic | 4 次请求、4 次 HTTP 404 错误，无有效回答 |
| AES adaptive | 4 次请求、4 次 HTTP 404 错误，无有效回答 |

默认测试与真实服务测试包含重复的集成测试入口，不能简单相加为 53 个独立通过用例。AES 请求均因文档不存在而失败，不应将其解释为模型回答正确率为 0；回答耗时、页码覆盖均记为不可用。

## 环境问题及处理

初次数据库集成测试为 1 passed、7 errors，原因是 PostgreSQL 无有效宿主机端口映射。尝试恢复 5432 后，Docker 报 Windows 端口绑定权限错误。使用本次实验的 compose-test-port.yaml 将同一 PostgreSQL 数据卷映射到 127.0.0.1:15432 后，API ready 检查的 PostgreSQL、MinIO、Milvus 均通过，8 项集成测试重跑通过。保留首次错误日志 integration.txt 和重跑日志 integration-retry.txt。

当前数据库保留旧知识库记录，但旧 AES 文档 d0c51025-edd4-43a8-ada8-2502bc46d74b 已不存在。使用现有 evaluate_rag.py 和未修改的 aes-regression.jsonl 执行两种模式，均得到 HTTP 404。未将历史 4 题结果或 430 文档结果挪用为本次实验结果。后续如需问答回归，需要重新导入并处理对应课件，再使用新的文档 ID。

数据库 .env 未改动。当前 Docker PostgreSQL 使用 15432，本机 API/worker 需要进程环境 POSTGRES_PORT=15432。复用本次端口配置：

```powershell
docker compose -f compose.yaml -f evaluation/results/run-20261029/compose-test-port.yaml up -d postgres
$env:POSTGRES_PORT='15432'
```

## 可用于简历的表述

> 基于 BGE-small 与 Milvus 完成 100 篇文档、634 个文本块的向量化和检索验证；结合持久化任务队列、租约栅栏及快照隔离机制，通过 7 项真实 PostgreSQL 集成测试和 1 项 Milvus 混合检索集成测试。

该描述将 100 文档参考检索验证与合成夹具集成测试分开表述，不暗示在 100 文档上已完成回答准确率评测。现阶段不填写回答准确率、召回提升或问答延迟；不写“全部检查通过”。

## 复现与原始证据

运行目录：evaluation/results/run-20261029。

- select_corpus.py / selection.json：选样规则与摘要。
- bge-small-100.json / bge-small-100.vectors.npy：真实检索结果与向量。
- default-tests.txt / default-tests.xml：默认测试。
- integration_runner.py / integration-retry.txt / integration.xml：真实服务测试。
- ruff.txt：静态检查未通过明细。
- aes-classic.json / aes-adaptive.json：逐题失败响应。
- summary.json：机器可读汇总。

```powershell
python -m scripts.verify_bge_small --folder evaluation/results/run-20261029/corpus100 --knowledge-base-id 27ad4b76-c515-4de0-8e97-051e520a9910 --output evaluation/results/run-20261029/bge-small-100.json
python -m pytest -q -p no:cacheprovider
$env:POSTGRES_PORT='15432'
python evaluation/results/run-20261029/integration_runner.py
```
