"""Local hi_res PDF parser. Retains page numbers, hierarchy and table HTML."""
#
import httpx
from bs4 import BeautifulSoup

from app.services.documents.visual_structure import rows_to_html, rows_to_markdown

# 解析表格 HTML
'''
value: 表格 HTML 内容
'''
def parse_table_html(value: str | None) -> dict | None:
    if not value:
        return None
    # 解析 HTML
    soup = BeautifulSoup(value, "html.parser")
    rows = []
    for row in soup.find_all("tr"):
        values = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if any(values):
            rows.append(values)
    if not rows:
        return None
    return {
        "rows": rows,
        "row_count": len(rows),
        "column_count": max(len(row) for row in rows),
        "markdown": rows_to_markdown(rows),
        "text_as_html": rows_to_html(rows),
    }

# 解析 PDF
'''
path: PDF 文件路径
task: 任务 ID
api_url: Unstructured API URL
page_count: PDF 页码数量
'''
def parse_pdf(path, task, api_url, page_count):
    with path.open("rb") as stream, httpx.Client(timeout=900) as client:
        # 发送请求
        response = client.post(
            api_url.rstrip("/") + "/general/v0/general",# 解析 PDF
            files={"files": (path.name, stream, "application/pdf")},# 上传 PDF 文件
            data={
                "strategy": "hi_res",# 解析策略
                "languages": "chi_sim,eng",# 语言
                "infer_table_structure": "true",# 推理表格结构
            },
        )
    # 检查响应状态码
    response.raise_for_status()
    # 解析 JSON 响应
    elements = response.json()
    if not isinstance(elements, list):
        raise TypeError("Unstructured 未返回元素列表")
    # 先按页数预建全部页，即使某页没解析出内容也保留空`blocks`
    pages = [{"page_number": p, "blocks": []} for p in range(1, page_count + 1)]
    # 提取所有标题元素
    titles = {e.get("element_id"): e for e in elements if e.get("type") == "Title"}
    # 遍历所有元素
    for i, element in enumerate(elements):
        # 提取元素元数据
        meta = element.get("metadata", {})
        # 提取元素页码
        number = meta.get("page_number")
        if not isinstance(number, int) or not 1 <= number <= page_count:
            raise ValueError("Unstructured 页码缺失或越界，未发布结果")
        # 提取文本内容
        text = element.get("text", "").strip()
        if not text:
            # 图片元素
            if str(element.get("type", "")).casefold() == "image":
                pages[number - 1]["blocks"].append(
                    {
                        "block_id": f"us-{i:06d}",
                        "source": "unstructured",
                        "kind": "image",
                        "text": "[图片]",
                        "heading_path": [],
                        "include_in_retrieval": False,
                        "coordinates": meta.get("coordinates"),
                        "needs_review": True,
                    }
                )
            continue
        # heading：标题路径，包含所有父标题文本
        heading, visited, parent = [], set(), meta.get("parent_id")
        # 构建标题路径
        while parent in titles and parent not in visited:
            visited.add(parent)# 标记为已访问
            heading.append(titles[parent].get("text", ""))# 添加标题文本
            parent = titles[parent].get("metadata", {}).get("parent_id")# 获取父元素 ID
        # 反转标题路径
        heading.reverse()
        if element.get("type") == "Title":
            heading.append(text) # 添加当前标题文本
        block = {
            "block_id": f"us-{i:06d}",
            "source": "unstructured",
            "kind": element.get("type"),
            "text": text,
            "heading_path": heading,
            "include_in_retrieval": True,
            "text_as_html": meta.get("text_as_html"),
            "coordinates": meta.get("coordinates"),
            "needs_review": False,
        }
        # 解析表格 HTML
        if str(element.get("type", "")).casefold() == "table":
            table = parse_table_html(meta.get("text_as_html"))
            if table is not None:
                block["table"] = table
                block["text_as_html"] = table["text_as_html"]
                block["text"] = "\n".join(value for value in [table["markdown"], text] if value)
        pages[number - 1]["blocks"].append(block)
    return {
        "stage": "merged",
        "schema_version": 1,
        "source_filename": task["filename"],
        "source_sha256": task["sha256"],
        "page_count": page_count,
        "pages": pages,
        "parser": "unstructured-hi_res",
        "limitations": [
            "hi_res 不是流程图关系验证器；未结构化提取箭头语义，不保证低分辨率图的正确性"
        ],
    }
