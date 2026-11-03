from docx import Document
from pathlib import Path
d=Document(r'C:\Users\珅珅\Desktop\刘宗珅_大模型应用开发实习简历.docx')
changes={
8:'• 编程基础：熟悉 Python，具备 C、Java 编程基础；掌握数据结构、计算机网络、操作系统等计算机基础知识。',
9:'• 大模型应用：熟悉 LangChain、LangGraph、ReAct 与工具调用；具备 RAG、多 Agent 编排、结构化输出校验及人工确认流程开发经验。',
10:'• 后端工程：使用 FastAPI 开发接口，结合 PostgreSQL、Milvus、MinIO 管理元数据、向量和文件；具备 Git、Docker Compose 部署及接口联调经验。',
13:'构建文档上传、解析、索引与跨文档问答流程，支持来源引用、检索纠错及异步任务处理。',
14:'• 混合检索：保留 Markdown 标题层级与来源位置，结合父子章节合并和语义分块组织文本；融合 Milvus Dense 与 BM25 检索结果，通过 RRF 与 BGE 重排序筛选证据。',
15:'• 问答流程：基于 LangGraph 实现复合问题拆分、多查询检索和相关性判断；结合答案依据检查、有限重试与证据不足拒答，支持带来源引用的跨文档回答。',
16:'• 任务可靠性：基于 PostgreSQL 实现持久化任务队列，使用失败重试、租约栅栏及断点式导入处理任务中断；按知识库、文档和版本隔离检索，防止旧快照及不同向量模型混用。',
17:'• 工程验证：接入 FastAPI、MinIO 与前端状态展示，提供语料审计、回归评估及旧快照清理工具；已有验收中默认测试 45 项通过、9 项跳过，真实 PostgreSQL 集成测试 7 项通过。',
19:'构建 Python 代码审查与修复工作流，将静态分析、上下文取证、人工确认及修复验证接入 Agent。',
20:'• 多 Agent 编排：基于 LangGraph 构建主助手及安全、缺陷、规范、性能、修复 5 类子助手，按职责分配工具；通过状态栈与检查点实现任务交接和中断恢复。',
21:'• 结论可追溯：封装静态分析、AST 与代码库 BM25 检索工具，校验审查结论的证据、工具调用记录及代码行号，过滤无依据结论；分离只读与敏感操作，执行敏感操作前引入人工确认。',
22:'• 执行与修复：实现 ReAct 运行时，限制执行步数、工具调用次数、Token 与耗时；修复代码依次经过语法、接口兼容及受限子进程行为验证，失败时反馈重试或回退原代码。',
23:'• 接入与观测：提供 REST API、SSE、MCP Server 与 VS Code 插件入口，记录工具调用、耗时及 Token 轨迹；支持单 Agent、多 Agent 和静态规则基线的评估。'
}
for i,text in changes.items():
 p=d.paragraphs[i]
 if p.runs:
  p.runs[0].text=text
  for r in p.runs[1:]:r.text=''
 else:p.add_run(text)
d.save('outputs/resume/刘宗珅_大模型应用开发实习简历_优化版.docx')
