"""Compound-question planning and deterministic retrieval fusion."""

import re

COMPOUND_MARKERS = re.compile(
    r"(分别|比较|对比|各自|以及|同时|另外|此外|并说明|再说明|哪些.*哪些)"
)


def looks_compound(question: str) -> bool:
    text = question.strip()
    if COMPOUND_MARKERS.search(text):
        return True
    return len(re.findall(r"[？?]", text)) > 1 or (
        "；" in text and len(text) >= 16
    )


def merge_query_hits(hit_groups, top_k: int, min_score: float):
    merged = {}
    total = 0
    for hits in hit_groups:
        total += len(hits)
        for hit in hits:
            entity = hit["entity"]
            key = (entity.get("document_id"), entity["chunk_id"])
            prior = merged.get(key)
            score = float(hit.get("rerank_score", hit.get("distance") or 0))
            if prior is None or score > float(
                prior.get("rerank_score", prior.get("distance") or 0)
            ):
                merged[key] = hit
    ranked = sorted(
        merged.values(),
        key=lambda hit: float(hit.get("rerank_score", hit.get("distance") or 0)),
        reverse=True,
    )
    selected = [
        hit for hit in ranked if float(hit.get("rerank_score", 0)) >= min_score
    ][:top_k]
    return selected, total
