# 2026-10-23 验收记录

本记录只写实际执行的检查。mock 测试不能替代真实服务结果，未执行的项目明确列为未完成。

## 已通过

| 项目 | 环境 | 结果 |
|---|---|---|
| Python 与静态检查 | Windows，Python 3.11.17，rag-kb | `ruff` 通过；默认测试 45 passed、9 skipped |
| 有界多进程任务队列 | Windows spawn，2 consumers | 4 个任务由 2 个真实子进程消费；异常任务进入失败处理并释放槽位 |
| PostgreSQL 真实队列 | 本机 PostgreSQL | 7/7，包括双 worker 领取、租约 fencing、失败保留旧快照、事务回滚、删除和唯一内容约束 |
| Milvus 真实混合检索 | 本机 Milvus 2.6.4 | 1/1，验证 Dense/BM25 同范围、旧快照隔离、跨文档过滤、快照删除和整文档清理 |
| MinIO | 本机 MinIO | live 检查 200，桶存在，`health/ready` 通过 |
| BGE-large 文档问答 | DeepSeek `deepseek-chat` | 经典、Agent、自适应均返回带真实页码引用的回答 |
| 原版 graph1 | DeepSeek `deepseek-chat` | `bind_tools → ToolNode → 相关性 → 生成` 实际返回第 43 页引用 |
| 独立 graph2 | DeepSeek `deepseek-chat` | 路由、检索、筛选、生成、依据检查、有效性检查和结束节点通过 |
| 复合查询拆分 | 真实知识库 | EUV/TSV 拆为两个子问题，答案引用 4 个不同文件 |
| 表格/图表/连接器 | 合成 PPTX + 实际课件 | 表格 Markdown 与结构化行、图表类别/系列、连接器起止关系及来源结构均通过测试 |
| OpenAI embedding 接口 | MockTransport + 阈值测试 | 请求/响应解析、短章节零请求、长章节触发 splitter 均通过 |
| 多轮追问 | 同一 `session_id` | “它/其中”追问正确解析为行移位，并重新引用第 44 页 |
| 跨文档问答 | 真实知识库 | EUV 与 TSV 比较回答引用 4 个不同文件 |
| 基础模式回归 | 4 道 AES 题 | 0 次请求错误；3 道有答案，程序覆盖均值 0.75；平均 6.77 秒 |
| 自适应回归 | 4 道 AES 题 | 0 次请求错误；3 道有答案；平均 11.41 秒 |
| 有限重试 | 自适应“字节代替下一步” | 2 次 rewrite 后 `insufficient_evidence`，没有伪造引用 |
| 语义分块与超长检查 | 已发布清单与 BGE tokenizer | 本轮报告的块未发现超过 512 token；历史大模型按实际缓存配置检查 |
| BGE-small 512 维 | 430 个 Markdown，真实 Milvus | 2682 块全部完成向量，命中 `tech_report_cp7b0kn4.md`，相似度约 0.806 |
| 批量重复数据 | 实际 434 文档 | 清理 50 个重复副本，复查重复组为 0；数据库创建唯一内容约束 |
| 文档删除 | 实际 Milvus/MinIO | 删除后 DB 记录、任务、索引、原文件和尝试产物清理通过 |
| 前端语法 | Node `--check` | 通过；答案与来源使用安全 Markdown DOM 渲染 |

结果文件：

- `evaluation/results/aes-classic.json`
- `evaluation/results/aes-adaptive.json`
- `evaluation/results/bge-small-512.json`
- `evaluation/results/corpus-audit-before.json`
- `evaluation/results/dedupe-corpus.json`
- `evaluation/results/corpus-audit-after-dedupe.json`

## 人工核对结论

- 行移位答案的行号、左移字节数和第 44 页引用一致。
- 列混淆答案明确写出矩阵乘法并引用第 45 页。
- S 盒答案给出的 16×16、95→2A 和逆 S 盒均与课件一致。
- 跨文档回答分别标注不同文件，并明确说明比较表是归纳而非原文现成结论。
- “字节代替下一步”没有因第 43/44 页相邻而错误放行，属于保守拒答。

## 未完成或受外部条件限制

| 项目 | 原因 | 下一步 |
|---|---|---|
| OpenAI embedding 超过 5000 字符真实请求 | 配置和客户端已实现并通过 MockTransport；本机没有真实 embedding 凭据 | 配置 provider、密钥和模型后重新处理长文 |
| RAGAS 真实评分 | 主环境没有 RAGAS 依赖，且不能伪造分数 | 在独立 `rag-eval` 环境安装 `evaluation/requirements-ragas.txt` 后运行 |
| Tavily 真实联网回退 | `TAVILY_API_KEY` 为空，服务开关关闭 | 配置密钥、设置 `RAG_WEB_ENABLED=true`，再测试允许/拒绝两条路径 |
| 低清流程箭头关系 | 现有 OCR 在第 42 页只保留少量可信文字，箭头语义缺失 | 使用人工复核图或更高分辨率/专门图解析；当前保持拒答 |
| 用户、角色、集群 | 未确认属于原项目要求 | 不在本轮擅自扩展 |

RAGAS 命令：

```powershell
python scripts/evaluate_ragas.py `
  --input evaluation/results/aes-adaptive.json `
  --output evaluation/results/aes-ragas.csv
```

真实回归命令：

```powershell
python -m scripts.evaluate_rag --knowledge-base-id <KB_ID> `
  --dataset evaluation/datasets/project-regression.jsonl `
  --output evaluation/results/project-adaptive.json --mode adaptive
```
