# 启动指南

项目根目录：`C:\Users\珅珅\Documents\trae_projects\rag_enterprise_knowledge_base`

所有命令都在**项目根目录**执行（`pyproject.toml` 和 `alembic.ini` 都在根目录）。

```powershell
cd $env:USERPROFILE\Documents\trae_projects\rag_enterprise_knowledge_base
```

---

## 1. 启动基础设施（Docker）

```powershell
docker compose up -d
docker compose ps
```

三个容器全部 `Up`（postgres / minio 显示 `(healthy)`，milvus 首次启动约需 60~90 秒）：

| 服务 | 容器名 | 端口 |
|------|--------|------|
| PostgreSQL | rag-postgres | 5432 |
| MinIO | rag-minio | 9000（API）/ 9001（控制台） |
| Milvus | rag-milvus | 19530（gRPC）/ 9091（健康检查） |

验证：

```powershell
curl.exe -s http://127.0.0.1:9091/healthz          # 期望输出 OK
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:9000/minio/health/live   # 期望 200
```

## 2. 激活 conda 环境

本机 `conda activate` 有 bug（PowerShell 7 + 旧版 `Conda.psm1` 传空参数），请用下面这条：

```powershell
(& "C:\tools\anaconda3\Scripts\conda.exe" "shell.powershell" "activate" "rag-kb") | Out-String | Invoke-Expression
```

验证激活成功（提示符出现 `(rag-kb)`）：

```powershell
python -c "import sys; print(sys.executable)"
# 期望：C:\tools\anaconda3\envs\rag-kb\python.exe
```

## 3. 安装依赖（首次或依赖变更时）

```powershell
python -m pip install -e ".[dev]"
```

⚠️ 必须在**项目根目录**执行。`package-dir = {"" = "backend"}`，所以 `app` 包会被正确安装。

## 4. 初始化/升级数据库

```powershell
alembic upgrade head
```

验证表已建好：

```powershell
docker compose exec postgres psql -U rag_user -d rag_kb -c "\dt"
```

## 5. 启动后端 API

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

成功标志：

```
Uvicorn running on http://127.0.0.1:8080 (Press CTRL+C to quit)
Application startup complete.
```

⚠️ **不要用 8000 端口**。Windows 的 Hyper-V / WSL 保留了 `7979-8078` 段（含 8000、8001），绑定会报 `[Errno 13] 以一种访问权限不允许的方式做了一个访问套接字的尝试`。

查看当前保留段：

```powershell
netsh int ipv4 show excludedportrange protocol=tcp
```

## 6. 启动后台处理 worker

**另开一个终端**（同样先激活 rag-kb 环境）：

```powershell
python -m app.tasks.worker
```

负责异步解析、切块、向量化、入库等耗时任务。不启动时，上传的文档会停在待处理状态。

需要多个本地消费者时，可显式启用有界 `multiprocessing.Queue`：

```powershell
python -m app.tasks.worker --multiprocessing-queue --workers 2
```

PostgreSQL 仍是持久化任务来源，主进程只领取任务并通过 `multiprocessing.Queue` 分发给子进程；每个消费者使用一个单任务槽位，避免本地预取超过数据库租约能力。每个消费者会独立加载 embedding 模型，`workers` 增加会同时增加内存和显存占用，默认仍使用单 worker。

## 7. 验证服务

| 地址 | 期望结果 |
|------|---------|
| http://127.0.0.1:8080/ | 欢迎 JSON |
| http://127.0.0.1:8080/ui/ | 前端页面（注意末尾斜杠） |
| http://127.0.0.1:8080/docs | Swagger 交互文档 |
| http://127.0.0.1:8080/api/v1/health | `{"status":"ok","service":"rag-enterprise-knowledge-base"}` |
| http://127.0.0.1:8080/api/v1/health/ready | `status=ready`，并包含 `database/minio/milvus/unstructured` |

`database`、`minio`、`milvus` 均为 `ok` 才表示核心问答依赖就绪。未启用 `RAG_USE_UNSTRUCTURED` 时，其值为 `disabled`。

## 8. 脚本工具（可选）

```powershell
# 检查已上传文档的原生 PPTX 内容
python -m scripts.inspect_pptx --document-id <uuid>

# 生成向量并交互式检索
python -m scripts.embed_document --input <merged-document.json> --knowledge-base-id <uuid> --document-id <uuid>

# 检索 + 重排序 + 生成带引用回答
python -m scripts.ask_document --manifest <embedding-manifest.json> --candidate-k 20 --top-k 5 --rerank-min-score 0.3
```

`ask_document` 的关键参数：

| 参数 | 默认 | 含义 |
|------|------|------|
| `--candidate-k` | 20 | Milvus 初步召回数量（1~50） |
| `--top-k` | 5 | 最终交给大模型的段落数（1~10） |
| `--rerank-min-score` | 0.3 | 重排序保留阈值，0 表示不过滤 |
| `--reranker-device` | cpu | 本机 torch 为 CPU 版，只能用 cpu |

## 9. 停止

```powershell
# 后端 / worker：在对应终端按 Ctrl+C
docker compose stop        # 停容器，数据保留
docker compose start       # 重新启动已停止的容器
docker compose down        # 删容器和网络，数据保留
docker compose down -v     # 连数据卷一起删 ⚠️ 数据全丢
```

---

## 环境注意事项

1. **torch 是 CPU 版**（`2.14.1+cpu`，`cuda False`）。`.env` 里 `EMBEDDING_DEVICE=cuda` / `RERANKER_DEVICE=cuda` 与实际情况不符，跑本地模型时请改用 `cpu` 或 `auto`。

2. **`RAG_LLM_MODEL=deepseek-flash` 不是有效模型名**。DeepSeek 官方只有 `deepseek-chat` 和 `deepseek-reasoner`，否则调用返回 `400 Model Not Exist`。

3. **MinIO 镜像是冻结版本**（`RELEASE.2025-09-07T16-13-09Z`）。MinIO 自 2025-10-15 起停止在 Docker Hub / Quay.io 发布镜像，compose 里已设 `pull_policy: never`，只能用本机已有镜像。换机器前先备份：
   ```powershell
   docker save minio/minio:RELEASE.2025-09-07T16-13-09Z -o minio-image.tar
   ```

4. **端口 8000/8001 不可用**（见第 5 节），统一用 8080。

5. **`.env` 不提交 Git**，换机器时从 `.env.example` 复制后填写密钥。

6. **模型默认离线加载**。`MODEL_LOCAL_FILES_ONLY=true` 时，Hugging Face 不会因外网检查卡住；缺少本机缓存应明确失败。首次下载模型时再临时设为 `false`。

---

## 10. 大批量语料补跑

常规 worker 为每个任务启动独立子进程，隔离性好，但每份 Markdown 都会重新加载 BGE。已有多份 queued 文件可按顺序批量补跑：

```powershell
python -m scripts.process_queued_corpus --reschedule
```

它仍使用 PostgreSQL 任务、租约、发布 fencing 和相同切块/入库代码，只是整个批次复用一份 BGE。补跑报告写入 `storage/backfill-reports/`。

只处理一份做冒烟检查：

```powershell
python -m scripts.process_queued_corpus --reschedule --limit 1
```

## 11. 语料与索引核对

```powershell
python -m scripts.audit_corpus `
  --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae `
  --folder md `
  --output evaluation/results/corpus-audit.json
```

报告包含源文件数、唯一内容、重复、状态、空文档、块数、标题路径、待复核块、Token 超限块，以及当前生效快照在 Milvus 的实际条数。

幂等导入先 dry-run：

```powershell
python -m scripts.import_corpus `
  --folder md `
  --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae `
  --dry-run
```

同一知识库内相同 SHA-256 内容不会被重复上传；已有失败记录会重新入队。

## 12. 清理旧版本

默认只报告：

```powershell
python -m scripts.gc_retrieval --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae
```

确认报告后实际执行：

```powershell
python -m scripts.gc_retrieval --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae --apply
```

只删除非当前向量快照、旧 MinIO 尝试产物和旧本地尝试目录，保留当前生效快照。清理后会再次校验当前快照条数。

## 13. 原项目 BGE-small 512 维验收

```powershell
python -m scripts.verify_bge_small `
  --folder md `
  --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae `
  --output evaluation/results/bge-small-512.json
```

首次下载模型加 `--download`；之后去掉它进行离线复跑。脚本使用独立临时集合，不会改动主应用 1024 维索引。

## 14. 完整回归

```powershell
python -m scripts.evaluate_rag `
  --knowledge-base-id 536c7f30-06ec-4c86-9217-4d4b2e2b41ae `
  --dataset evaluation/datasets/project-regression.jsonl `
  --output evaluation/results/project-adaptive.json `
  --mode adaptive
```

跨文档样本必须在整个知识库范围运行，不要传 `--document-id`。页码覆盖、关键词和引用文件数只是自动检查；最终仍需人工核对答案与原文。

## 15. 原版 graph1 / graph2

前端“问答模式”可直接选择：

- `graph1`：`bind_tools → ToolNode → 相关性判断 → 改写/生成`。
- `graph2`：独立的路由、检索、文档过滤、改写、网络、生成、依据和有效性检查。
- `agent` / `adaptive`：保留原有工程兼容路径。

```powershell
python -m scripts.ask_knowledge --knowledge-base-id <KB_ID> --mode graph1
```

复合问题会拆成最多 6 个独立检索子问题，分别召回和重排，再按 chunk 去重融合。

## 16. OpenAI 兼容语义 embedding

默认保持 BGE：

```dotenv
RAG_SEMANTIC_EMBEDDING_PROVIDER=bge
```

启用 OpenAI 兼容接口：

```dotenv
RAG_SEMANTIC_EMBEDDING_PROVIDER=openai
RAG_SEMANTIC_OPENAI_THRESHOLD=5000
OPENAI_EMBEDDING_BASE_URL=https://api.openai.com/v1
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_API_KEY=你的密钥
```

该设置只影响语义断点；写入 Milvus 的最终向量仍使用当前 BGE 配置。不超过阈值的章节不会发起 OpenAI 请求。
