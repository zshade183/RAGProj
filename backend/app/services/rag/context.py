"""Evidence is rebuilt only from an active, verified manifest, never from stale hit text."""


def expand_context(selected, snapshots, max_chars=14000, max_sources=18, radius=1):
    chunks, by_page = {}, {}
    for manifest, snapshot_id in snapshots:
        for c in manifest["chunks"]:
            if (
                c.get("document_id") != manifest["document_id"]
                or c.get("knowledge_base_id") != manifest["knowledge_base_id"]
            ):
                raise ValueError("快照块不属于当前文档")
            key = (manifest["document_id"], c["chunk_id"])
            chunks[key] = (c, snapshot_id)
            by_page.setdefault((key[0], c["page_number"]), []).append(c)
    output, seen, used = [], set(), 0

    def add(chunk, sid, distance, kind):
        nonlocal used
        key = (chunk["document_id"], chunk["chunk_id"])
        if key in seen or len(output) >= max_sources:
            return
        # Omit flagged OCR spans, retaining native text. Original chunks stay untouched.
        spans = chunk.get("source_blocks", [])
        text = chunk["text"]
        bad = sorted(
            (s["chunk_char_start"], s["chunk_char_end"])
            for s in spans
            if s.get("source") == "ocr" and s.get("needs_review")
        )
        if bad:
            chars = list(text)
            for a, b in bad:
                chars[a:b] = [""] * (b - a)
            text = "".join(chars).strip()
        if not text or used + len(text) > max_chars:
            return
        seen.add(key)
        used += len(text)
        output.append(
            {
                "distance": distance,
                "retrieval_kind": kind,
                "entity": {**chunk, "text": text, "snapshot_id": sid},
            }
        )

    seeds = []
    for hit in selected:
        e = hit["entity"]
        key = (e.get("document_id"), e["chunk_id"])
        # Legacy Milvus output lacks doc_id; only resolve unambiguous identities.
        matches = [v for k, v in chunks.items() if k[1] == e["chunk_id"]] if key[0] is None else []
        item = chunks.get(key) or (matches[0] if len(matches) == 1 else None)
        if item is None:
            raise ValueError("检索结果不在当前快照中")
        c, sid = item
        if e.get("snapshot_id", sid) != sid:
            raise ValueError("检索结果版本不一致")
        add(c, sid, hit["distance"], "retrieved")
        seeds.append((c, sid))
    # Add same-page continuation and one physical page on either side, in seed order.
    for delta in [0] + [d for r in range(1, radius + 1) for d in (r, -r)]:
        for c, sid in seeds:
            for neighbor in by_page.get((c["document_id"], c["page_number"] + delta), []):
                add(neighbor, sid, None, "adjacent")
    return output
