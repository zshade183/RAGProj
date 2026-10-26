"""Local hi_res PDF parser. Retains page numbers, hierarchy and table HTML."""

import httpx
from bs4 import BeautifulSoup

from app.services.documents.visual_structure import rows_to_html, rows_to_markdown


def parse_table_html(value: str | None) -> dict | None:
    if not value:
        return None
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


def parse_pdf(path, task, api_url, page_count):
    with path.open("rb") as stream, httpx.Client(timeout=900) as client:
        response = client.post(
            api_url.rstrip("/") + "/general/v0/general",
            files={"files": (path.name, stream, "application/pdf")},
            data={
                "strategy": "hi_res",
                "languages": "chi_sim,eng",
                "infer_table_structure": "true",
            },
        )
    response.raise_for_status()
    elements = response.json()
    if not isinstance(elements, list):
        raise TypeError("Unstructured 未返回元素列表")
    pages = [{"page_number": p, "blocks": []} for p in range(1, page_count + 1)]
    titles = {e.get("element_id"): e for e in elements if e.get("type") == "Title"}
    for i, element in enumerate(elements):
        meta = element.get("metadata", {})
        number = meta.get("page_number")
        if not isinstance(number, int) or not 1 <= number <= page_count:
            raise ValueError("Unstructured 页码缺失或越界，未发布结果")
        text = element.get("text", "").strip()
        if not text:
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
        heading, visited, parent = [], set(), meta.get("parent_id")
        while parent in titles and parent not in visited:
            visited.add(parent)
            heading.append(titles[parent].get("text", ""))
            parent = titles[parent].get("metadata", {}).get("parent_id")
        heading.reverse()
        if element.get("type") == "Title":
            heading.append(text)
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
