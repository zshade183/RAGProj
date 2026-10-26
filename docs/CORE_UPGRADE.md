# 原项目核心 RAG 更新：安装、学习与验收

这次更新针对你上传的 RAG_PROJECT 源码：标题层级解析、语义分块、BM25 + Dense 混合检索、LangGraph 检索纠错、网络搜索和生成检查。它是接入现有 FastAPI 项目的功能复现，不是逐文件复制原仓库。

## 1. 本次完成了哪些代码

| 原项目能力 | 接入位置 | 行为 |
|---|---|---|
| Unstructured Markdown 元素解析 | services/documents/semantic.py | 提取标题 ancestry，按标题合并段落，保留原块来源 |
| SemanticChunker | 同上 | 95 百分位断点，BGE 本地语义向量；超长窗口分段平均归一化，最终块满足模型 token 预算 |
| Dense + BM25 + RRF | services/retrieval/hybrid_store.py | 新集合 v2、jieba 分词、DAAT_MAXSCORE、RRF k=100；随后使用既有 reranker |
| graph1 风格流程 | services/rag/workflow.py 的 agent 模式 | 结构化路线选择、检索相关性判断、查询改写循环、回答；会话检查点 |
| graph2 风格流程 | 同上 adaptive 模式 | 知识库/网络路线、文档判断、改写、生成、依据检查、回答有效性检查 |
| 网络回退 | services/rag/decisions.py | Tavily；服务器与请求均允许才执行；返回独立网络来源和 URL |
| 邻页上下文 | services/rag/context.py | 当前已验证快照内同页与前后各一页；独立来源编号，限制总长度，跳过标记待审的 OCR 片段 |
| 跨文档检索 | api/v1/endpoints/knowledge_rag.py | 只允许同一知识库中每份文档的当前有效版本；全部需混合索引 |
| 会话 | workflow + api_service | 最近6轮、最多32个活跃会话；进程内检查点；重启清空 |
| hi_res 服务 | deploy/unstructured + unstructured_client.py | 可选 Docker 服务；PDF/转换后的 Office 文档，保存页码、标题路径、表格 HTML |
| 回归评估 | scripts/evaluate_rag.py | 请求真实 API，保存答案、证据、耗时与覆盖检查 |
| RAGAS | scripts/evaluate_ragas.py | 独立环境运行，真实评估 faithfulness/context_recall；配置 embedding API 后增加 answer_relevancy |

保留现有 bge-large 1024 维，避免改回原代码 bge-small 512 维而使所有旧数据失效。SemanticChunker 使用本地 BGE 而不是原代码中的 OpenAI embedding。Agent 路线用结构化 JSON 决策，不要求服务支持 tool_calls；实现了原流程分支，但不宣称逐行一致。

页面顺序不等于算法步骤顺序；邻页扩展不把“第43页后面是第44页”当作算法顺序证据。PPTX 的低清流程图 OCR 尚未通过质量验收，hi_res 也不是自动理解所有箭头的保证。

## 2. 安装更新

在 Windows 项目根目录，确认提示符为 `(rag-kb)`。先在 API 和 worker 窗口 Ctrl+C 停止进程，保留 Docker 服务运行。

将 ZIP 解压到项目根目录，得到 `rag-core-update` 文件夹。先检查，再安装：

```powershell
python .\rag-core-update\apply_update.py --project-root .
python .\rag-core-update\apply_update.py --project-root . --apply
```

安装器先检查所有文件。能精确匹配的局部修改会合并；重叠冲突会列出文件并停止，不强行覆盖。它会备份原文件到 `storage/update-backups/`。不修改 `.env`，不删除数据，不执行索引迁移。

保存当前依赖清单，再安装：

```powershell
python -m pip freeze > requirements-before-core.txt
python -m pip install -e ".[dev]"
python -m nltk.downloader punkt_tab averaged_perceptron_tagger_eng
python -m pip check
python -m pytest -q
```

NLTK 数据供 Unstructured 的文本分类使用。下载失败应先解决下载问题；不要把空解析结果当成功。Windows 如果提示找不到 libmagic，可在当前环境执行 `python -m pip install python-magic-bin` 后重试。NLTK 数据缺失的集成测试会明确显示 skipped。

本次没有新增数据库表；前一版自动处理的 Alembic 迁移必须已经完成。

## 3. 配置

在现有 `.env` 中添加以下设置；同名项只保留一份：

```dotenv
RAG_SEMANTIC_CHUNKING=true
RAG_USE_UNSTRUCTURED=false
RAG_WEB_ENABLED=false
RAG_MAX_REWRITES=2
RAG_MAX_GENERATIONS=3
```

- 语义分块：只影响之后处理的文档。已有块不会自动改变。
- 重写最多2次，生成最多3次；失败最终返回证据不足。有限重试不保证答案必然正确。
- 联网：需要设置 `RAG_WEB_ENABLED=true` 和有效的 `TAVILY_API_KEY`，并在网页勾选允许联网。发送给搜索服务的是检索问题，可能包含会话改写后的实体信息。
- 继续使用你自己的 `RAG_LLM_BASE_URL`、`RAG_LLM_MODEL`、`RAG_LLM_API_KEY`。代码没有写死供应商。
- `经典/基础`模式保持一次检索生成；`agent`增加相关性评估和改写；`adaptive`再增加答案依据和问题匹配检查。评估调用增加耗时与 API 用量。

## 4. 将已有向量迁移成混合索引，无需重跑 OCR

Docker 中 PostgreSQL、MinIO、Milvus 需要运行。API/worker 暂时保持停止。

```powershell
python -m scripts.migrate_hybrid --all
```

或只迁移课件：

```powershell
python -m scripts.migrate_hybrid --document-id d0c51025-edd4-43a8-ada8-2502bc46d74b
```

脚本校验向量文件哈希，复用已有 BGE 向量，写入新集合 `rag_bge_large_zh_v15_hybrid_v2`。只有写入数量正确且文档没有并发变化时，才切换数据库中的快照指针。旧集合保留。迁移记录保存在 `storage/index-migration-backups/`。

迁移不会改善旧 OCR，也不会改变旧分块。之后点击“重新处理”才启用新的语义分块。不要用旧的 `scripts/milvus_document.py` 向新快照入库；新流程由 worker 或 migrate_hybrid 负责。旧 `scripts/ask_document.py` 是旧索引调试工具，新问答请用网页或 `scripts/ask_knowledge.py`。

## 5. 启动和浏览器验收

窗口一，项目根目录：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

窗口二，项目根目录：

```powershell
python -m app.tasks.worker
```

浏览器打开 `http://127.0.0.1:8080/ui/`，Ctrl+F5 强制刷新。

先选择课件和“自适应纠错”。问：

- “课件中行移位如何处理四行？”应取得第44页依据。
- “列混淆由什么运算实现？”应取得第45页依据。
- “字节代替下一步是什么？”仍需核对第42页的流程图证据；邻页文字只能辅助，不能据此宣布 OCR 完成。

应看到 `hybrid_rrf`，以及 `route → retrieve → grade_documents → generate → verify → finish` 等真实节点轨迹。证据不足时应出现 rewrite；无依据回答不会无限重试。

再问追问，确认同一会话能够解析指代。点击“新会话”后不继承旧历史。切换文档、模式、联网权限或快照版本会隔离会话。基础模式不使用历史。

“整个知识库”模式跨文档检索，不会跨知识库或检索旧版本。若知识库内存在旧索引文档，先迁移，接口不会偷偷遗漏它。尚未处理成功、没有发布快照的文件不参与问答。

CLI 使用相同 API：

```powershell
python -m scripts.ask_knowledge --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae --mode adaptive
```

实际端口不同则追加 `--base-url http://127.0.0.1:你的端口`。

## 6. 原始 Markdown 语料

解压原项目的 `md.zip`，把 `--folder` 改成实际目录：

```powershell
python -m scripts.import_corpus --folder "C:\你的路径\md" --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae
```

该命令递归上传所有 Markdown；worker 逐个解析和入库。重复执行会重复上传，不要重复运行。大批量文件在你的本机需要较长处理时间；本实现按一个 worker 串行处理以控制内存。

## 7. 可选 hi_res 解析

```powershell
docker compose -f compose.unstructured.yaml --profile ocr up -d --build
```

在 `.env` 中设置：

```dotenv
RAG_USE_UNSTRUCTURED=true
UNSTRUCTURED_API_URL=http://127.0.0.1:8001
```

重启 worker 后，重新处理目标 PDF/PPTX/DOCX。容器第一次处理需下载布局模型；16GB 内存下不要同时启动多个 OCR worker。此服务保留表格 HTML 元数据，但当前网页仍展示文本证据。配置为 false 时继续用已有本地 Tesseract 路径，不强制占用 Docker OCR 资源。

## 8. 评估和测试

真实课件回归：

```powershell
python -m scripts.evaluate_rag --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae --document-id d0c51025-edd4-43a8-ada8-2502bc46d74b --dataset evaluation/datasets/aes-regression.jsonl --output evaluation/results/aes-adaptive.json
```

不要把页码覆盖率或关键词命中率叫做“回答准确率”。结果包含全部证据和答案，需核对引用内容是否支持结论。

RAGAS 使用独立环境，避免与主应用的 LangChain 依赖冲突：

```powershell
conda create -n rag-eval python=3.11 -y
conda activate rag-eval
python -m pip install -r evaluation/requirements-ragas.txt
python scripts/evaluate_ragas.py --input evaluation/results/aes-adaptive.json --output evaluation/results/aes-ragas.csv
conda activate rag-kb
```

默认评估 faithfulness 和 context_recall。若要增加 answer_relevancy，设置独立 `EVAL_EMBEDDING_MODEL`、`EVAL_EMBEDDING_BASE_URL`、`EVAL_EMBEDDING_API_KEY`。聊天模型接口不等于 embedding 接口。评估会调用你配置的大模型；没有实际运行时不会生成任何评分结论。

本机 Milvus 集成测试（仅建立和清理一个随机命名测试集合）：

```powershell
$env:TEST_MILVUS_URI="http://127.0.0.1:19530"
python -m pytest backend/tests/integration/test_hybrid_milvus.py -v
Remove-Item Env:TEST_MILVUS_URI
```

## 9. 回滚

如已迁移索引，先停止 worker，使用迁移打印的记录恢复旧快照指针：

```powershell
python -m scripts.restore_snapshot --record "storage/index-migration-backups/实际记录.json"
```

逐个恢复需要回退的文档，再停止 API，用安装器打印的代码备份目录回滚：

```powershell
python .\rag-core-update\apply_update.py --project-root . --rollback "storage/update-backups/实际备份目录"
```

回滚检查文件是否在安装后又被修改，存在变化则拒绝覆盖。代码回滚不会自动卸载依赖，不删除任何索引。若已经用新分块重新处理，迁移记录的版本检查可能拒绝回退，应保留数据并单独处理，不能强行改指针。

## 10. 验证范围与差异

自动化测试覆盖图的成功与失败分支、有限重试、内存会话隔离、相邻页来源、Milvus 两路过滤条件、解析来源位置以及原有后台任务回归。API 使用真实 LangGraph 执行；LLM、向量模型和网络搜索在单元测试中使用替身。

交付环境没有你的真实 PostgreSQL/Milvus/MinIO 服务与大模型密钥，所以没有声称已完成 Windows 全链路验收，也没有生成虚构的 RAGAS 分数。实际课件原生文字邻页测试已执行；低清流程图 OCR 质量问题仍存在。

与原项目保持一致的算法主线已接入。仍保留本机应用边界：一次一个 RAG 请求，会话为进程内内存；不宣称这是已完成权限、集群扩容和生产运维的企业平台。

技术依据：
- https://milvus.io/docs/v2.6.x/full-text-search.md
- https://milvus.io/docs/v2.6.x/multi-vector-search.md
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.tavily.com/documentation/api-reference/endpoint/search
- https://docs.ragas.io/en/v0.3.3/references/evaluate/
