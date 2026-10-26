# 自动文档处理升级说明

## 这次交付

在上传的 20261021-170725 源码基础上修改，不包含 .env、真实课件、模型、数据库或 Docker 数据卷。

- PostgreSQL processing_jobs 持久化队列：上传成功与入队在同一事务提交。
- 一个文档一条任务记录：重复点击处理不会创建并行任务。
- 独立 worker + 每次尝试独立子进程：默认顺序处理，完成后释放模型内存。
- 每 5 秒续租，默认租约 90 秒；中断后可领取过期任务；最多 3 次尝试，失败间隔 30/60 秒。
- 独立尝试 token、行锁、发布前租约校验：旧进程不能激活过期结果。
- 下载并校验原文件，解析/OCR、合并、过滤、切块、Embedding、Milvus 写入及数量核对。
- 处理产物备份到 MinIO 的 artifacts/文档ID/尝试token/，成功后原子更新 documents 的生效快照。
- 新处理失败不覆盖旧快照；原 rag_sources.json 保持兼容。
- 网页每 3 秒刷新当前文档状态，提供处理/重试按钮。
- 将实际运行依赖从 dev 移入 dependencies，补上 python-dotenv 与 BeautifulSoup。

## 1. 应用更新

先停止 API、旧问答程序和 worker。将更新 ZIP 解压到项目目录以外。
在 PowerShell 执行（修改解压路径）：

```powershell
& "C:\你的解压目录\rag-auto-processing-update\Apply-Update.ps1" -ProjectRoot "C:\Users\珅珅\Documents\trae_projects\rag_enterprise_knowledge_base"
```

脚本先校验待覆盖文件是否仍与上传版本一致；如果你上传后又改过这些文件，它会停止并列出冲突，不会强行覆盖。每个原文件会备份到项目 storage/update-backups/时间戳/。新文件和原文件变更清单见 CHANGESET.json。

此脚本不会执行数据库迁移或安装依赖，也不会改 .env、rag_sources.json 和 Docker 数据卷。文件备份目录中的 manifest.json 记录哪些是新增文件。回滚代码时恢复备份文件并删除这些新增文件；必须先停止 worker/API，数据库回滚另见下文。

## 2. 依赖与数据库迁移

回到项目根目录，激活原环境：

```powershell
conda activate rag-kb
python -m pip install -e ".[dev]"
docker compose up -d postgres minio milvus
```

先备份数据库（不会删除数据）：

```powershell
docker compose exec -T postgres pg_dump -U rag_user -d rag_kb -f /tmp/rag_before_worker.sql
docker cp rag-postgres:/tmp/rag_before_worker.sql .\rag_before_worker.sql
```

该备份包含文档元数据，请留在本机并加入 Git 忽略。然后：

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic current
```

新 revision 为 cb20261021，接在你源码中的 7922292ec554 后。新增 processing_jobs 表、documents.active_manifest 与 active_snapshot_id，不删除旧表数据。

## 3. 检查 .env 的 worker 配置

旧配置保持原样。以下配置有默认值，路径不同才必须调整；在 .env 中使用单引号包裹 Windows 路径：

```dotenv
PROCESSING_ENABLED=true
LIBREOFFICE_PATH='C:\Program Files\LibreOffice\program\soffice.com'
TESSERACT_PATH='C:\Program Files\Tesseract-OCR\tesseract.exe'
OCR_LANGUAGES=chi_sim+eng
WORKER_EMBEDDING_DEVICE=cpu
WORKER_POLL_SECONDS=3
WORKER_LEASE_SECONDS=90
WORKER_MAX_ATTEMPTS=3
WORKER_TASK_TIMEOUT=7200
```

CPU 默认是为了避免后台入库与 API Embedding 抢显存。需要 GPU 且 PyTorch 支持 CUDA 时可设置 WORKER_EMBEDDING_DEVICE=cuda。不要同时开启多份旧问答程序。本机先只运行一个 worker 和一个 API 进程。

```powershell
& "C:\Program Files\LibreOffice\program\soffice.com" --version
& "C:\Program Files\Tesseract-OCR\tesseract.exe" --list-langs
```

Tesseract 语言列表必须包含 chi_sim 和 eng。中文缺失将明确失败，不能静默退化成只识别英文。

## 4. 启动两个终端

两个终端都在项目根目录，均先 conda activate rag-kb。

终端 A（API）：

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

终端 B（worker）：

```powershell
python -m app.tasks.worker
```

浏览器打开 http://127.0.0.1:8000/ui/ 并按 Ctrl+F5。

没有 worker 时文件仍能上传，任务保留在 queued；启动 worker 后自动消费。PROCESSING_ENABLED=false 只关闭上传自动入队，网页处理按钮仍可手动入队。

## 5. 第一轮验收：短 TXT

先建一个 UTF-8 编码 TXT，内容例如“测试产品的保修期为24个月。报修时需要提供购买凭证。”
通过网页上传，观察：排队 → 下载 → 解析 → 生成向量 → 写入向量库 → ready。
首次运行需要下载/加载 BGE 模型，不要看到较长等待就重复上传。ready 后问“测试产品保修期多久？”应引用刚上传的文档。

TXT/MD/HTML 的第1页是逻辑文档页，不是实际打印页。PDF 保留 PDF 页码；DOCX 的页码以 LibreOffice 渲染结果为准。

## 6. 第二轮验收：真实 PPTX

选择已有课件，点击“处理 / 重新处理”。不用重新上传。
会执行完整渲染、每页 OCR、原生文字合并与质量过滤，再向量化。即使之前手动产物存在，本轮也重新生成，不冒用旧缓存。
完成后不再需要编辑 rag_sources.json。数据库快照优先，旧手动登记仅在数据库尚无快照时兼容使用。

任务查询接口：

GET /api/v1/knowledge-bases/{kb}/documents/{doc}/processing
POST /api/v1/knowledge-bases/{kb}/documents/{doc}/process

## 7. 失败与恢复验收

- 停止 worker，上传测试文件：应显示 queued，重启 worker 后继续。
- 在处理中于 worker 终端 Ctrl+C：尝试子进程会被终止；重启后最多等待一个租约周期，任务重新开始。
- 重复点击处理：在 queued/running 时返回同一任务，不同时执行第二次。
- 使 Tesseract 路径无效并重启 worker：PPTX 任务应失败并自动重试，最多3次，之后显示 failed。
- 恢复路径，重启 worker，点击重试：尝试计数归零并再次处理。
- 已有生效快照的文档重新处理失败：旧快照应继续可问答。

每次尝试的诊断文件：storage/processed/文档ID/尝试token/pipeline.log、progress.json、failure.json。
页面展示失败阶段、异常类型和相对日志位置；详细异常在本机日志中。
自动重试从头执行，不是逐页断点续跑。原始文件上传过程中崩溃留下的 uploading 记录不自动判定成功，需核查原文件后处理。

## 8. 自动化检查

```powershell
python -m ruff check backend scripts
python -m pytest -v
```

本次交付环境：Linux / Python 3.12（你的目标环境仍为 Python 3.11）。17项测试通过，5项真实 PostgreSQL 测试因未提供服务而跳过。测试包含 mock，不能代替 Windows/LibreOffice/Tesseract/MinIO/Milvus/BGE 的真实端到端验收。前端完成 Node 语法检查，未做浏览器端到端自动化。Alembic 已生成全迁移链 PostgreSQL SQL，未在你的数据库执行。

若要运行真实锁与事务测试，将 TEST_DATABASE_URL 设置为专用测试 PostgreSQL 数据库连接（postgresql+asyncpg://...），再执行：

```powershell
python -m pytest backend/tests/integration/test_processing_postgres.py -v
```

测试创建唯一临时 schema，测试后删除该 schema，不操作 public 表。需要 CREATE SCHEMA 权限。建议使用专用测试数据库，URL 密码特殊字符需要 URL 编码。勿把连接串发到聊天或提交 Git。

## 边界与后续工作

- 本轮已有 PPTX 全流程；PDF/DOCX 使用渲染页文字和 OCR，尚未重建复杂表格与版面；原生 PPTX 表格继续复用既有提取器。公式/图表不保证完整理解。
- 暂停删除已进入任务流程的文档（409），避免删除时仍有 worker 写向量。任务取消、向量与对象联合清理仍待实现。纯未处理文档沿用原删除逻辑。
- 老快照及失败尝试保留，不自动清理；占用空间随重处理增加。后续加入保留策略和垃圾回收。
- 本版本要求 API 与 worker 共用同一项目文件系统。产物已备份到 MinIO，但 API 暂时仍读取本地生效清单；尚非多机部署。
- 网页与 API 仍只适合本机开发，未增加用户权限、租户隔离和完整审计。
- 租约保证旧任务不发布，但强制杀死 worker 进程时，子进程可能短暂继续运行；独立尝试目录与快照隔离防止它覆盖新生效版本。

## 回滚

先停止 API 与 worker。若已产生自动处理任务，回滚将丢失任务记录和生效快照指针，应先导出/保留数据库备份及产物，再决定是否执行：

```powershell
python -m alembic downgrade 7922292ec554
```

恢复 storage/update-backups/ 中的原代码并删除本次新增代码；原来的 rag_sources.json 不变，可继续使用旧手动流程。不要使用 docker compose down -v 回滚，它会删除数据卷。
