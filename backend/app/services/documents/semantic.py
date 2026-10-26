"""Heading-aware Markdown parsing and provenance-preserving semantic chunks."""

import hashlib
from uuid import uuid5

import numpy as np
from langchain_core.embeddings import Embeddings
from langchain_experimental.text_splitter import SemanticChunker

from app.services.documents.chunking import build_chunks

SEMANTIC_VERSION = "heading-semantic-percentile-v2"


def markdown_page(path):
    from langchain_community.document_loaders import UnstructuredMarkdownLoader

    docs = UnstructuredMarkdownLoader(str(path), mode="elements", strategy="fast").load()
    titles = {
        d.metadata.get("element_id"): d
        for d in docs
        if d.metadata.get("category") == "Title" and d.metadata.get("element_id")
    }

    def ancestry(item):
        values, visited = [], set()
        parent = item.metadata.get("parent_id")
        while parent and parent not in visited and parent in titles:
            visited.add(parent)
            title = titles[parent]
            values.append(title.page_content.strip())
            parent = title.metadata.get("parent_id")
        return list(reversed(values))

    blocks = []
    for i, doc in enumerate(docs):
        text = doc.page_content.strip()
        if not text:
            continue
        heading = ancestry(doc)
        if doc.metadata.get("category") == "Title":
            heading.append(text)
        blocks.append(
            {
                "block_id": f"md-{i:06d}",
                "source": "native",
                "kind": doc.metadata.get("category", "text"),
                "text": text,
                "heading_path": heading,
                "include_in_retrieval": True,
                "text_as_html": doc.metadata.get("text_as_html"),
            }
        )
    return {"page_number": 1, "blocks": blocks}


class BgeSemanticAdapter(Embeddings):
    """Bound long SemanticChunker windows before embedding, without silent truncation."""

    def __init__(self, embedder):
        self.embedder = embedder

    def embed_documents(self, texts):
        output = []
        for text in texts:
            pieces = self._pieces(text)
            vectors = self.embedder.encode_documents(pieces, batch_size=2)
            vector = np.average(vectors, axis=0, weights=[len(p) for p in pieces])
            norm = np.linalg.norm(vector)
            if not np.isfinite(norm) or norm == 0:
                raise ValueError("语义分块向量异常")
            output.append((vector / norm).tolist())
        return output

    def _pieces(self, text):
        if self.embedder.token_count(text) <= self.embedder.max_tokens:
            return [text]
        if len(text) < 2:
            raise ValueError("单字符超出模型限制")
        mid = len(text) // 2
        return self._pieces(text[:mid]) + self._pieces(text[mid:])

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def semantic_chunks(
    document,
    kb_id,
    doc_id,
    embedder,
    semantic_embedder=None,
    openai_threshold: int | None = None,
):
    split_embedding = semantic_embedder or BgeSemanticAdapter(embedder)
    splitter = SemanticChunker(
        split_embedding,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=95,
        sentence_split_regex=r"(?<=[。！？.!?])\s*|\n+",
        buffer_size=1,
    )
    # Form sections within each page; never lose physical-page provenance.
    grouped = []
    for page in document["pages"]:
        groups = []
        for block in page["blocks"]:
            if not block.get("include_in_retrieval"):
                continue
            heading = block.get("heading_path", [])
            if not groups or groups[-1][0] != heading:
                groups.append((heading, []))
            groups[-1][1].append(block)
        grouped.append((page, groups))
    result = []
    for page, groups in grouped:
        for section_index, (heading, blocks) in enumerate(groups):
            # Existing chunker gives exact source spans for the whole section.
            mini = {**document, "page_count": 1, "pages": [{"page_number": 1, "blocks": blocks}]}
            large = build_chunks(mini, kb_id, doc_id, chunk_size=10**9, overlap=0)[0]
            text = large["text"]
            if (
                semantic_embedder is not None
                and openai_threshold is not None
                and len(text) <= openai_threshold
            ):
                segments = [text]
            else:
                segments = splitter.split_text(text)
            # SemanticChunker joins sentences with spaces. Map its normalized output
            # back to original character offsets, preserving source text exactly.
            positions = [i for i, char in enumerate(text) if not char.isspace()]
            normalized = "".join(text[i] for i in positions)
            cursor = 0
            for segment in segments:
                needle = "".join(segment.split())
                if not needle:
                    continue
                if not normalized.startswith(needle, cursor):
                    raise ValueError("语义分块来源无法对齐")
                start = positions[cursor]
                cursor += len(needle)
                end = positions[cursor - 1] + 1
                ranges = [(start, end)]
                while ranges:
                    a, b = ranges.pop(0)
                    part = text[a:b]
                    prefix = " / ".join(heading)
                    prefix = prefix[:120] + "\n" if prefix else ""
                    if embedder.token_count(prefix + part) > embedder.max_tokens:
                        if b - a <= 1:
                            raise ValueError("标题和正文超过 token 限制")
                        middle = (a + b) // 2
                        ranges[0:0] = [(a, middle), (middle, b)]
                        continue
                    if not part.strip():
                        continue
                    spans = []
                    for span in large["source_blocks"]:
                        lo = max(a, span["chunk_char_start"])
                        hi = min(b, span["chunk_char_end"])
                        if lo < hi:
                            spans.append(
                                {
                                    **span,
                                    "block_char_start": span["block_char_start"]
                                    + lo
                                    - span["chunk_char_start"],
                                    "block_char_end": span["block_char_start"]
                                    + hi
                                    - span["chunk_char_start"],
                                    "chunk_char_start": len(prefix) + lo - a,
                                    "chunk_char_end": len(prefix) + hi - a,
                                }
                            )
                    value = prefix + part
                    sha = hashlib.sha256(value.encode()).hexdigest()
                    identity = f"{SEMANTIC_VERSION}:{document['source_sha256']}:{page['page_number']}:{section_index}:{a}:{b}:{sha}"
                    result.append(
                        {
                            **large,
                            "chunk_id": str(uuid5(doc_id, identity)),
                            "page_number": page["page_number"],
                            "text": value,
                            "char_count": len(value),
                            "content_sha256": sha,
                            "chunk_index": len(result),
                            "heading_path": heading,
                            "section_index": section_index,
                            "section_char_start": a,
                            "section_char_end": b,
                            "page_char_start": None,
                            "page_char_end": None,
                            "source_blocks": spans,
                            "needs_review": any(s.get("needs_review") for s in spans),
                            "token_count": embedder.token_count(value),
                        }
                    )
    if not result:
        raise ValueError("没有可分块文本")
    return result
