from docx import Document
from docx.shared import Pt,Cm,RGBColor
from docx.oxml.ns import qn
d=Document(); s=d.sections[0]
s.page_width=Cm(21);s.page_height=Cm(29.7)
s.top_margin=Cm(1.3);s.bottom_margin=Cm(1.3);s.left_margin=Cm(1.6);s.right_margin=Cm(1.6)
st=d.styles['Normal'];st.font.name='Calibri';st.font.size=Pt(10)
st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体')
st.paragraph_format.line_spacing=1.05;st.paragraph_format.space_after=Pt(3)
def p(t,k='body'):
 x=d.add_paragraph();x.paragraph_format.widow_control=True
 if k in ('title','heading','project'):
  if k=='title':x.style='Title';x.alignment=1
  r=x.add_run(t);r.bold=True;r.font.size=Pt({'title':21,'heading':12,'project':10.5}[k]);r.font.color.rgb=RGBColor(0,0,0)
  x.paragraph_format.space_before=Pt(6 if k!='title' else 0);x.paragraph_format.space_after=Pt(4);x.paragraph_format.keep_with_next=True
 else:
  if '：' in t:
   a,b=t.split('：',1);x.add_run(a+'：').bold=True;x.add_run(b)
  else:x.add_run(t)
 return x
p('刘宗珅','title')
p('大模型应用开发实习生 / Agent 开发实习生').alignment=1
p('18263919367  |  zshade@qq.com').alignment=1
p('教育背景','heading')
p('中国科学院大学 · 信息工程研究所  |  网络与信息安全  |  硕士在读')
p('山东大学  |  计算机科学与技术  |  本科  |  2022—2026')
p('本科成绩：89.13/100，专业排名 11/28；CET-6：463 分。')
p('荣誉奖项：2023 年优秀学生一等奖学金、校级三好学生；2025 年优秀学生三等奖学金。')
p('项目经历','heading')
p('企业知识库 RAG 系统','project')
p('技术栈：Python、FastAPI、LangGraph、Milvus、BGE、PostgreSQL、MinIO')
p('项目简介：面向企业文档检索与跨文档问答，构建从文件上传、异步解析、向量入库到引用溯源的完整流程，支持基础问答与自适应纠错模式。')
p('• 检索与证据组织：结合标题层级、父子章节合并和语义分块组织文档；将复合问题拆为子查询，融合 Dense 与 BM25 召回，经 RRF、BGE 重排序和去重筛选证据，并扩展相邻页上下文，补充跨段落信息。')
p('• 自适应问答：基于 LangGraph 串联路由、检索、相关性判断、生成和验证；检查引用是否支持答案、是否覆盖各子问题，按失败原因触发查询改写或重新生成，设置重试上限并在证据不足时拒答。')
p('• 异步任务可靠性：使用 PostgreSQL 持久化队列和 SKIP LOCKED 支持多 worker 领取任务，通过心跳续租、令牌校验及失败重试处理进程中断；发布失败时保留旧快照，阻止过期 worker 覆盖新结果。')
p('• 数据一致性与验收：按知识库、文档和快照统一过滤两路检索，使用 SHA-256 去重及唯一约束控制重复入库；已有验收中 7 项真实 PostgreSQL 集成测试通过，跨文档问答案例引用 4 个不同文件。')
p('代码审查与修复 Agent','project')
p('技术栈：Python、LangGraph、ReAct、Pylint、Bandit、AST、BM25、MCP')
p('项目简介：面向 Python 代码构建审查、取证、修复和验证流程，输出包含问题位置、证据及修复建议的结构化报告，并提供 API 与 VS Code 插件接入。')
p('• 多 Agent 协作：通过 LangGraph 主助手委派安全、缺陷、规范、性能 4 类审查助手及修复助手，按角色裁剪工具；利用状态栈与检查点管理交接和恢复，通过 reducer 合并、去重各维度结论。')
p('• 工具取证与可信输出：封装静态分析、AST 和代码库 BM25 检索工具；在收集节点校验 JSON 契约、行号范围、证据内容及本轮工具调用记录，丢弃无证据结论，使报告可追溯到代码和工具结果。')
p('• 执行控制与修复验证：实现独立 ReAct 运行时，以步数、工具次数、Token 和耗时约束执行；在图中隔离敏感工具并中断等待人工确认，结合语法、接口兼容和受限子进程验证检查修复结果，失败时重试或回退。')
p('• 可观测与评估：通过 REST API / SSE、MCP Server 和 VS Code 插件接入审查流程，记录工具调用与资源消耗轨迹；构建基于 50 条合成样本的静态工具、单次模型调用及完整 Agent 三方案评估入口。')
p('专业技能','heading')
p('• LLM 应用：熟悉 LangChain / LangGraph、ReAct、Tool Calling；具备文档分块、Embedding、混合检索、Rerank、引用校验和多 Agent 工作流开发经验。')
p('• 后端与工程：熟悉 Python，具备 C、Java 基础；使用 FastAPI、PostgreSQL、Milvus、MinIO 开发服务，具备 Git、Docker Compose 部署及接口联调经验。')
p('• 计算机基础：掌握数据结构、操作系统、计算机网络等基础知识。')
d.save('outputs/resume/刘宗珅_大模型应用开发实习简历_重构版.docx')
print('Created rewritten resume')
