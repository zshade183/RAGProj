from docx import Document
from copy import deepcopy
d=Document(r'C:/Users/珅珅/Desktop/刘宗珅_大模型应用开发实习简历.docx')
changes={
13:'项目简介：基于 FastAPI、LangGraph、Milvus 与 PostgreSQL 构建企业文档问答系统，打通文档解析、异步入库、混合检索与答案溯源，支持跨文档综合问答。',
14:'• RAG 检索优化：针对复合问题拆分子查询，分别执行 Dense + BM25 检索，经 RRF 融合、BGE 重排序及去重筛选证据；结合标题层级、语义分块与相邻页上下文扩展，补充单块检索缺失的上下文。',
15:'• 自适应问答：基于 LangGraph 编排路由、检索、相关性判断、生成与验证节点；检查引用依据及子问题覆盖情况，按失败原因触发有限改写或重新生成，证据不足时拒答，控制无依据输出。',
16:'• 异步任务与版本隔离：使用 PostgreSQL 持久化队列管理多进程任务，通过租约栅栏、失败重试及断点式导入处理任务中断；按知识库、文档与快照过滤检索，防止过期任务写入和旧版本证据混用。',
17:'• 工程验收：接入 MinIO 文件存储与处理状态展示，提供语料审计、回归评估及快照清理工具；已有验收中 7 项真实 PostgreSQL 集成测试通过，跨文档问答案例引用 4 个不同文件。',
19:'项目简介：面向 Python 代码审查，构建 LangGraph 多智能体工作流与独立 ReAct 运行时，将静态分析、代码检索、证据校验和修复验证接入审查流程。',
20:'• Multi-Agent 编排：由主助手委派安全、缺陷、规范、性能 4 类审查助手及修复助手；通过状态栈、检查点与 reducer 管理交接、恢复和结论去重，按角色分配工具权限。',
21:'• 工具取证与结论校验：结合静态分析、AST 和代码库 BM25 检索获取上下文；在收集节点校验 JSON 契约、行号范围、证据及本轮工具调用记录，丢弃无依据结论，汇总结构化审查报告。',
22:'• 执行约束与修复验证：为 ReAct 设置步数、工具次数、Token 和耗时预算；通过 LangGraph 中断机制对敏感工具执行进行人工确认，结合语法、接口兼容及受限子进程验证检查修复结果，失败后重试或回退。',
23:'• 接入与评估：提供 REST API / SSE、MCP Server 及 VS Code 插件入口，记录调用与资源消耗轨迹；建立静态规则、单次模型调用和完整 Agent 三方案评估入口，统一样本与类别、行号匹配口径。'
}
for i,t in changes.items():
 p=d.paragraphs[i]; fmt=deepcopy(p.runs[0]._r.rPr) if p.runs and p.runs[0]._r.rPr is not None else None
 for r in list(p.runs):p._p.remove(r._r)
 a,b=t.split('：',1)
 for value,bold in ((a+'：',True),(b,False)):
  r=p.add_run(value)
  if fmt is not None:r._r.insert(0,deepcopy(fmt))
  r.bold=bold
out='outputs/resume/刘宗珅_大模型应用开发实习简历_项目表述优化版.docx'
d.save(out)
original=Document(r'C:/Users/珅珅/Desktop/刘宗珅_大模型应用开发实习简历.docx')
assert all(p.text==d.paragraphs[i].text for i,p in enumerate(original.paragraphs) if i not in changes)
print('Verified: only 10 project paragraphs changed.')
