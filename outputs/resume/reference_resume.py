from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
src=Document('outputs/resume/刘宗珅_大模型应用开发实习简历_优化版.docx')
texts=[p.text for p in src.paragraphs]
d=Document()
s=d.sections[0]; s.page_height=Cm(29.7); s.page_width=Cm(21); s.top_margin=Cm(1.25); s.bottom_margin=Cm(1.25); s.left_margin=Cm(1.5); s.right_margin=Cm(1.5)
normal=d.styles['Normal']; normal.font.name='Calibri'; normal.font.size=Pt(10)
normal.element.rPr.rFonts.set(qn('w:eastAsia'),'宋体')
normal.paragraph_format.space_after=Pt(3); normal.paragraph_format.line_spacing=1.05

def add(t,kind='body'):
 p=d.add_paragraph(); p.paragraph_format.widow_control=True
 if kind=='title':
  p.style=d.styles['Title']; p.alignment=1
  r=p.add_run(t); r.bold=True; r.font.size=Pt(20); r.font.color.rgb=RGBColor(0,0,0)
 elif kind in ('section','project'):
  r=p.add_run(t); r.bold=True; r.font.size=Pt(12 if kind=='section' else 10.5)
  p.paragraph_format.keep_with_next=True; p.paragraph_format.space_before=Pt(7)
  if kind=='section':
   borders=OxmlElement('w:pBdr'); b=OxmlElement('w:bottom'); b.set(qn('w:val'),'single'); b.set(qn('w:sz'),'6'); b.set(qn('w:color'),'263C55'); borders.append(b); p._p.get_or_add_pPr().append(borders)
 else:
  if '：' in t:
   a,b=t.split('：',1); p.add_run(a+'：').bold=True; p.add_run(b)
  else:p.add_run(t)
 return p
add(texts[0],'title')
for i in (1,2):add(texts[i]).alignment=1
add('教育背景','section')
for i in (4,5,6):add(texts[i])
add('在校荣誉：'+texts[25])
add('项目经历','section')
add('RAG 企业知识库','project')
add('技术栈：Python、FastAPI、LangGraph、Milvus、BGE、PostgreSQL、MinIO、Docker Compose')
add('项目简介：'+texts[13])
for i in range(14,18):add(texts[i])
add('代码审查 Agent','project')
add('技术栈：Python、LangGraph、ReAct、AST、BM25、REST API / SSE、MCP')
add('项目简介：'+texts[19])
for i in range(20,24):add(texts[i])
add('专业技能','section')
for i in (8,9,10):add(texts[i])
d.save('outputs/resume/刘宗珅_大模型应用开发实习简历_参考版.docx')
print('Saved; paragraphs:',len(d.paragraphs))
