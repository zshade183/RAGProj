"""Bounded Agentic / Adaptive RAG graphs with optional conversation checkpoints."""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    question: str
    query: str
    mode: str
    allow_web: bool
    route: str
    hits: list
    evidence: list
    result: dict
    history: list
    rewrites: int
    generations: int
    web_used: bool
    decision: str
    trace: list
    retrieved_count: int
    accepted_count: int
    queries: list


def build_workflow(
    retrieve, expand, answering, judge, web, checkpointer=None, max_rewrites=2, max_generations=3
):
    def event(s, name, **data):
        return {"trace": s.get("trace", []) + [{"node": name, **data}]}

    def route(s):
        query = s["question"]
        if s.get("history"):
            query = judge.rewrite(query, query, s["history"][-6:])
        target = judge.route(query, s["allow_web"], s["mode"])
        if target == "web" and not s["allow_web"]:
            target = "retrieve"
        return {"query": query, "route": target, **event(s, "route", target=target)}

    def direct(s):
        answer = answering.complete(
            "只简短回应问候、致谢或情绪表达。不要回答任何专业事实或文档问题；"
            "若不是日常交流，请请用户明确知识问题。",
            s["question"],
            max_tokens=200,
        )
        return {
            "result": {
                "status": "direct",
                "answer": answer,
                "citation_ids": [],
                "sources": [],
                "citation_check": "not_applicable",
            },
            **event(s, "direct"),
        }

    def retrieve_node(s):
        output = retrieve(s["query"])
        if len(output) == 3:
            hits, count, queries = output
        else:
            hits, count = output
            queries = [s["query"]]
        return {
            "hits": hits,
            "evidence": [],
            "queries": queries,
            "retrieved_count": s["retrieved_count"] + count,
            **event(s, "retrieve", candidates=count, queries=queries),
        }

    def grade_documents(s):
        relevant = [
            h
            for h in s["hits"]
            if judge.relevant(
                h.get("matched_query", s["query"]),
                h["entity"]["text"],
            )
        ]
        evidence = expand(relevant) if relevant else []
        decision = "generate" if evidence else fallback(s)
        return {
            "evidence": evidence,
            "accepted_count": len(evidence),
            "decision": decision,
            **event(s, "grade_documents", relevant=len(relevant), evidence=len(evidence)),
        }

    def fallback(s):
        if s["rewrites"] < max_rewrites and s["generations"] < max_generations:
            return "rewrite"
        if s["allow_web"] and not s["web_used"] and s["generations"] < max_generations:
            return "web"
        return "insufficient"

    def rewrite(s):
        query = judge.rewrite(s["question"], s["query"], s.get("history", [])[-6:])
        return {
            "query": query,
            "rewrites": s["rewrites"] + 1,
            **event(s, "rewrite", attempt=s["rewrites"] + 1),
        }

    def web_node(s):
        hits = web.search(s["query"])
        relevant = [h for h in hits if judge.relevant(s["query"], h["entity"]["text"])]
        return {
            "web_used": True,
            "evidence": relevant,
            "accepted_count": len(relevant),
            "decision": "generate" if relevant else "insufficient",
            **event(s, "web", results=len(relevant)),
        }

    def generate(s):
        result = answering.answer(
            s["query"],
            s["evidence"],
            subquestions=s.get("queries", []),
        )
        return {
            "result": result,
            "generations": s["generations"] + 1,
            **event(s, "generate", attempt=s["generations"] + 1),
        }

    def verify(s):
        result = s["result"]
        grounded = bool(result["citation_ids"]) and judge.grounded(s["query"], result)
        useful = grounded and judge.useful(
            s["query"],
            result["answer"],
            s.get("queries", []),
        )
        if grounded and useful:
            decision = "finish"
        elif not grounded and s["generations"] < max_generations:
            decision = "generate"
        else:
            decision = fallback(s)
        return {"decision": decision, **event(s, "verify", grounded=grounded, useful=useful)}

    def insufficient(s):
        return {
            "result": {
                "status": "insufficient_evidence",
                "answer": "在限定的检索与纠错次数内，未找到足以可靠回答的证据。请补充资料或明确问题。",
                "citation_ids": [],
                "sources": [],
                "citation_check": "not_applicable",
            },
            **event(s, "insufficient"),
        }

    def finish(s):
        result = s["result"]
        status = result.get("status", "answered")
        result = {**result, "status": status}
        history = s.get("history", [])
        if status in {"answered", "direct"}:
            history = (history + [{"question": s["question"], "answer": result["answer"][:2000]}])[
                -6:
            ]
        return {"result": result, "history": history, **event(s, "finish")}

    graph = StateGraph(State)
    for name, fn in [
        ("route", route),
        ("direct", direct),
        ("retrieve", retrieve_node),
        ("grade_documents", grade_documents),
        ("rewrite", rewrite),
        ("web", web_node),
        ("generate", generate),
        ("verify", verify),
        ("insufficient", insufficient),
        ("finish", finish),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route", lambda s: s["route"], {"retrieve": "retrieve", "web": "web", "direct": "direct"}
    )
    graph.add_edge("direct", "finish")
    graph.add_edge("retrieve", "grade_documents")
    branches = {name: name for name in ["generate", "rewrite", "web", "insufficient"]}
    graph.add_conditional_edges("grade_documents", lambda s: s["decision"], branches)
    graph.add_edge("rewrite", "retrieve")
    graph.add_conditional_edges(
        "web", lambda s: s["decision"], {"generate": "generate", "insufficient": "insufficient"}
    )
    # Agent mode matches graph1's pre-generation relevance loop; adaptive adds graph2 checks.
    graph.add_conditional_edges(
        "generate",
        lambda s: "verify" if s["mode"] == "adaptive" else "finish",
        {"verify": "verify", "finish": "finish"},
    )
    graph.add_conditional_edges("verify", lambda s: s["decision"], {**branches, "finish": "finish"})
    graph.add_edge("insufficient", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


def initial_state(question, mode, allow_web):
    return {
        "question": question,
        "query": question,
        "mode": mode,
        "allow_web": allow_web,
        "hits": [],
        "evidence": [],
        "result": {},
        "rewrites": 0,
        "generations": 0,
        "web_used": False,
        "trace": [],
        "retrieved_count": 0,
        "accepted_count": 0,
        "queries": [],
        "decision": "",
        "route": "",
    }
