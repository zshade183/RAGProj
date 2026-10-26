# 原项目与当前工程对照

本表以压缩包中的 `RAG_PROJECT` 源码为依据。判断标准是“行为是否保留”，不是文件名是否相同。

| 能力 | 原始项目 | 当前实现 | 结论 |
|---|---|---|---|
| Markdown 元素解析 | Unstructured `mode=elements, strategy=fast` | 保留，并额外保存 `heading_path`、`block_id` 和字符区间 | 主线复现，修正父子标题顺序与来源丢失 |
| 长文档语义切分 | 仅 `len(page_content)>5000` 时使用 OpenAI embedding 的 `SemanticChunker` | 默认保持 BGE；设置 provider 为 OpenAI 后，仅长章节调用 OpenAI 兼容 embedding，并保证最终块不超 token 上限 | 配置开关已实现；真实服务尚无凭据 |
| BGE-small | `BAAI/bge-small-zh-v1.5`，512 维 | `scripts/verify_bge_small.py` 可独立复现 430 个源文件、2682 块和真实 Milvus 检索；主应用继续使用 1024 维 BGE-large | 功能保留但默认模型不同，避免破坏现有索引 |
| 稀疏检索 | Milvus BM25，jieba | 保留 BM25、Dense、RRF，并增加知识库/文档/当前快照过滤 | 主线复现，隔离增强 |
| 检索重排 | 原项目未形成独立安全重排层 | BGE reranker，严格检查 token 预算，不静默截断 | 工程增强 |
| graph1 | `bind_tools → ToolNode → 工具消息 → 相关性 → rewrite/generate` | 新增 `graph1` 模式，使用 `ChatOpenAI.bind_tools`、`ToolNode` 和 ToolMessage artifact，并保留有界改写 | 调用协议复现并修正无限循环 |
| graph2 | 路由、文档评分、改写、网络、生成、幻觉与有效性检查 | 新增独立 `graph2` 模式，采用原节点顺序并保留有界重试、引用检查和失败闭环 | 节点流程复现并修正无限循环 |
| Web 搜索 | Tavily，检索后直接拼接为一个文档 | 保留 Tavily 和 URL 来源；服务器开关与单次请求权限双重控制 | 行为复现，来源隔离增强 |
| 会话 | graph1 使用内存 `MemorySaver` | 进程内最多 32 个会话，最近 6 轮；按知识库、快照、模式、权限隔离 | 保留边界并增强隔离 |
| 索引更新 | 原始建集合代码会删除同名 collection | 版本化快照、写后计数、数据库围栏发布；旧快照保留并可回收 | 修正破坏性行为 |
| 生产者消费者入库 | 文件解析进程通过 `multiprocessing.Queue` 向 Milvus 写入进程发送批次 | PostgreSQL 负责持久化领取与租约，`--multiprocessing-queue` 使用每消费者单槽本地队列分发已领取任务 | 保留多进程消费模型，并以持久化队列补上崩溃恢复 |
| 删除 | 原项目没有完整应用级删除链路 | 先停止召回，再清理双索引、原文件、产物和任务；失败可重试 | 工程增强 |
| 文档类型 | 主要是 Markdown | 增加 PPTX/PDF/DOCX、OCR、Unstructured hi_res、结构化表格、图表数据和原生连接器关系 | 工程增强 |
| 页面/来源 | 原 Document 元数据有限 | 页码、文件、块、字符区间、快照和 URL 均可校验 | 工程增强 |

## 不应混同的模型配置

- 原项目语义切分使用 OpenAI embedding，BGE-small 用于向量库。
- 当前主应用默认语义切分与召回都使用 BGE-large；没有把聊天接口冒充 embedding 接口。
- BGE-small 的结果保存在独立验收报告中，没有写入主集合，也没有改主文档的生效指针。

## 当前保留差异

- graph1 已在真实 DeepSeek 上完成工具调用；测试使用可绑定工具的假模型复核消息协议。
- 低清 AES 流程图的“字节代替下一步”仍可能被安全检查拒绝。这是证据质量边界，不应通过放宽规则伪造关系。
- 当前 API 仍是单进程会话和全局 RAG 串行锁，不属于生产集群实现。
- PDF 位图中的图表关系和无端点箭头仍无法可靠结构化；当前可处理 PPTX 原生连接器及 Unstructured 表格 HTML。
