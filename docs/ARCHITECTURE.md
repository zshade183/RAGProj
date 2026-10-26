# 架构与数据边界

## 运行组件

```text
浏览器
  │
  ├─ 上传/处理/删除 API
  │    ├─ PostgreSQL: 知识库、文档元数据、任务栅栏、生效快照指针
  │    ├─ MinIO: 原文件、处理产物、尝试快照
  │    └─ processing_jobs: queued/running/succeeded/failed
  │
  ├─ worker / 可选 multiprocessing.Queue 多消费者 / 批量补跑
  │    └─ 隔离尝试目录
  │         ├─ 下载并校验 SHA-256
  │         ├─ Markdown 层级 / PPTX、PDF、DOCX 渲染与 OCR
  │         ├─ 质量过滤与来源位置保留
  │         ├─ BGE 语义切块和向量化
  │         └─ Milvus 写入与条数核对
  │
  └─ 问答 API
       ├─ 仅读取数据库中的当前生效清单
       ├─ Dense + BM25 + RRF
       ├─ BGE 重排
       ├─ LangGraph 决策
       └─ Chat Completions + 引用校验
```

## 数据职责

- PostgreSQL 负责事务状态和“哪一版可以发布”，不保存大段向量。
- MinIO 保存原始文件与可审计处理产物。
- Milvus 保存带 `knowledge_base_id`、`document_id`、`snapshot_id` 的向量行。
- 本地 `storage/` 是单机处理缓存与清单；API 必须能读取生效清单，因此当前仍要求 API 与 worker 共用项目目录。
- `multiprocessing.Queue` 只负责把已领取任务分发给本机消费者；持久化领取、租约、重试和发布许可仍由 PostgreSQL 任务表负责。

## 发布不变量

1. 下载文件摘要必须等于上传时记录的 SHA-256。
2. Milvus 写入条数必须等于清单块数，否则不发布。
3. 发布前任务 token 和租约必须仍属于当前 worker。
4. 新处理失败不覆盖旧 `active_manifest/active_snapshot_id`。
5. 删除先写 `deleting` 状态，使所有检索立即排除，再清理外部数据。
6. 检索过滤同时包含知识库、文档、当前快照；Dense 与 BM25 使用完全相同的 allowlist。
7. 回答中的引用编号必须属于本次 evidence；历史、邻页和网络来源不能混为一体。

## 检索与问答

Markdown 解析保存标题 ancestry，再按章节生成语义块。块中保留 `heading_path`、`source_blocks`、物理页码和字符区间。检索结果不能直接作为永久证据；回答前会从当前已验证清单重建证据并校验版本。

`classic` 执行一次检索和生成；`agent` 增加结构化路由、文档相关性判断、有限改写；`adaptive` 再执行依据检查和回答有效性检查。网络只有在服务器允许且单次请求允许时才走 Tavily。

## 失败恢复

- 每个尝试使用独立 token 和目录。
- 相同 token 的产物不会覆盖后来生效的版本。
- worker 租约过期后可由新 worker 领取；旧进程无法通过发布 fencing。
- 多进程消费者异常退出后，父进程将其在途任务交回数据库失败处理，后续按租约规则重试。
- 清理失败保留 `deleting` 墓碑，重试可继续；它不会再被问答召回。
- 批量补跑使用与常规 worker 相同的发布约束，只是复用 BGE 模型以减少冷启动。

## 运行边界

当前 API 使用进程内会话与单请求 RAG 锁，适合同机、单实例和验收。它不包含用户登录、角色权限、多租户、跨机共享会话或生产集群调度；这些能力只有原项目明确要求且经过设计后才会加入。
