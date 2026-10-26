# RAG 企业知识库

这是一个把原始 `RAG_PROJECT` 的 Markdown 解析、语义分块、BGE 向量、BM25 + Dense 混合检索和 LangGraph 纠错流程，接入 FastAPI、PostgreSQL、MinIO、Milvus 的可运行实现。

当前默认模型为 `BAAI/bge-large-zh-v1.5`（1024 维），并保留原项目 `BAAI/bge-small-zh-v1.5`（512 维）的独立验收入口。现有数据不因切换模型而静默混用；不同模型、维度和快照必须显式迁移。

## 当前能力

- 文档上传、去重、持久化任务队列、失败重试、租约栅栏和断点式批量导入。
- Markdown 标题层级、父子章节合并、来源位置保留及 BGE/OpenAI embedding 可切换语义分块。
- PPTX 表格、图表数据和带连接端点的形状关系提取，前端支持安全 Markdown 表格。
- Milvus BM25 + Dense 混合检索、RRF 融合与 BGE 重排。
- 经典、Agent、自适应工程模式，以及独立复现的 graph1 工具调用和 graph2 节点流程。
- 复合问题拆分、多查询检索、按子问题判断相关性、去重与分数融合。
- 多轮会话、新会话隔离、文档/知识库/版本/删除隔离。
- 跨文档问答、答案依据检查、回答有效性检查和可选 Tavily 网络回退。
- 前端安全 Markdown 渲染、文档处理状态轮询和来源展示。
- 原文件、向量快照、旧版本和安全删除的可重试清理。

## 快速启动

完整 Windows 步骤见 [start.md](start.md)。

```powershell
cd C:\Users\珅珅\Documents\trae_projects\rag_enterprise_knowledge_base
docker compose up -d
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

另开终端启动常驻 worker：

```powershell
python -m app.tasks.worker
```

需要本地多消费者时，可启用有界 `multiprocessing.Queue`：

```powershell
python -m app.tasks.worker --multiprocessing-queue --workers 2
```

PostgreSQL 仍负责持久化领取、租约和发布 fencing；本机子进程只消费已领取任务。消费者数量增加时，BGE 模型内存占用也会按进程增加。

浏览器打开 `http://127.0.0.1:8080/ui/`。就绪检查 `GET /api/v1/health/ready` 同时验证 PostgreSQL、MinIO 和 Milvus。

## 常用验收命令

```powershell
# 只读核对源文件、清单、截断和索引数量
python -m scripts.audit_corpus --knowledge-base-id <KB_ID> --folder md

# 幂等导入；先 dry-run，确认后去掉 --dry-run
python -m scripts.import_corpus --folder md --knowledge-base-id <KB_ID> --dry-run

# 大批量 Markdown 补跑：复用一份 BGE，避免每份文件重新加载
python -m scripts.process_queued_corpus --reschedule

# 真实 API 回归
python -m scripts.evaluate_rag --knowledge-base-id <KB_ID> --document-id <DOC_ID> `
  --dataset evaluation/datasets/aes-regression.jsonl `
  --output evaluation/results/aes-adaptive.json --mode adaptive

# 旧向量快照和旧尝试产物清理；默认只报告
python -m scripts.gc_retrieval --knowledge-base-id <KB_ID> --apply
```

`--reschedule` 会只重置现有 queued 任务的可领取时间，不改文档或历史快照。

## 测试

```powershell
python -m ruff check backend scripts
python -m pytest -q
```

真实 PostgreSQL 与 Milvus 测试需要显式设置 `TEST_DATABASE_URL`、`TEST_MILVUS_URI`，详见 [docs/ACCEPTANCE_20261023.md](docs/ACCEPTANCE_20261023.md)。

## 配置与边界

- 配置模板见 [.env.example](.env.example)，真实密钥只放在本机 `.env`。
- 默认 `MODEL_LOCAL_FILES_ONLY=true`，部署时不会因 Hugging Face 外网抖动卡住；首次下载模型时显式改为 `false` 或使用验收脚本的 `--download`。
- 会话历史保存在当前 API 进程内，服务重启后清空；未实现生产级用户登录、角色权限和集群调度。
- 低清流程图的箭头和步骤关系仍可能无法恢复。系统在证据不足时应拒答，不能把邻页标题当作流程图顺序。

原始实现与当前工程的逐项差异见 [docs/REFERENCE_DIFF.md](docs/REFERENCE_DIFF.md)。

按当前代码编写的完整实现说明见 [docs/PROJECT_IMPLEMENTATION.md](docs/PROJECT_IMPLEMENTATION.md)。
