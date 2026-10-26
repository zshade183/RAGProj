"""Reference graph1/graph2 node and edge structure adapted to the current storage layer."""

from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode, tools_condition


def make_chat_model(*, base_url: str, api_key: str, model: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        temperature=0,
        timeout=180,
        max_retries=1,
    )


def _event(state, name: str, **data) -> dict:
    return {"trace": state.get("trace", []) + [{"node": name, **data}]}


class Graph1State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    evidence: list
    accepted_count: int
    retrieved_count: int
    rewrites: int
    result: dict
    trace: list
    queries: list


def build_reference_graph1(
    *,
    llm: ChatOpenAI,
    retrieve,
    expand,
    answering,
    judge,
    max_rewrites: int,
    checkpointer=None,
):
    @tool("rag_retriever", response_format="content_and_artifact")
    def rag_retriever(query: str):
        """检索当前知识库当前有效版本的文档。输入应为独立、完整的中文检索问题。"""

        hits, count, queries = retrieve(query)
        content = json.dumps(
            {
                "query": query,
                "candidate_count": count,
                "queries": queries,
                "documents": [
                    {
                        "filename": hit["entity"]["source_filename"],
                        "page_number": hit["entity"].get("page_number"),
                        "text": hit["entity"]["text"],
                    }
                    for hit in hits
                ],
            },
            ensure_ascii=False,
        )
        return content, hits

    model = llm.bind_tools([rag_retriever])
    tool_node = ToolNode([rag_retriever])

    def agent(state):
        response = model.invoke(
            [
                SystemMessage(
                    content=(
                        "你是知识库检索 Agent。知识事实问题必须调用 rag_retriever；"
                        "只有纯寒暄、致谢或情绪表达可以不调用工具直接回复。"
                        "不得用常识替代知识库证据。"
                    )
                ),
                *state["messages"][-8:],
            ]
        )
        return {
            "messages": [response],
            **_event(
                state,
                "agent",
                tool_calls=len(getattr(response, "tool_calls", []) or []),
            ),
        }

    def grade_documents(state):
        last = state["messages"][-1]
        if not isinstance(last, ToolMessage):
            raise TypeError("graph1 检索节点没有返回 ToolMessage")
        hits = list(last.artifact or [])
        relevant = [
            hit
            for hit in hits
            if judge.relevant(
                hit.get("matched_query", state["question"]),
                hit["entity"]["text"],
            )
        ]
        evidence = expand(relevant) if relevant else []
        return {
            "evidence": evidence,
            "accepted_count": len(evidence),
            "retrieved_count": state.get("retrieved_count", 0) + len(hits),
            **_event(
                state,
                "grade_documents",
                relevant=len(relevant),
                evidence=len(evidence),
            ),
        }

    def retrieve_node(state):
        result = tool_node.invoke(state)
        messages = result.get("messages", [])
        tool_message = messages[-1] if messages else None
        return {
            **result,
            **_event(
                state,
                "retrieve",
                tool_message=isinstance(tool_message, ToolMessage),
            ),
        }

    def decide(state):
        return "generate" if state.get("evidence") else "rewrite"

    def rewrite(state):
        response = llm.invoke(
            [
                *state["messages"][-6:],
                HumanMessage(
                    content=(
                        "请把原问题改写为更适合知识库检索的独立问题。"
                        "保留实体和限制，不回答问题，只输出改写后的问题。"
                    )
                ),
            ]
        )
        text = response.content
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("graph1 查询改写为空")
        return {
            "messages": [AIMessage(content=text.strip())],
            "rewrites": state.get("rewrites", 0) + 1,
            **_event(state, "rewrite", attempt=state.get("rewrites", 0) + 1),
        }

    def decide_after_rewrite(state):
        return "agent" if state.get("rewrites", 0) < max_rewrites else "insufficient"

    def generate(state):
        result = answering.answer(
            state["question"],
            state["evidence"],
            subquestions=state.get("queries", []),
        )
        result["status"] = "answered"
        return {
            "result": result,
            "messages": [AIMessage(content=result["answer"])],
            **_event(state, "generate"),
        }

    def insufficient(state):
        return {
            "result": {
                "status": "insufficient_evidence",
                "answer": "在限定的工具检索和改写次数内，未找到足以可靠回答的证据。",
                "citation_ids": [],
                "sources": [],
                "citation_check": "not_applicable",
            },
            **_event(state, "insufficient"),
        }

    graph = StateGraph(Graph1State)
    graph.add_node("agent", agent)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("rewrite", rewrite)
    graph.add_node("generate", generate)
    graph.add_node("insufficient", insufficient)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "retrieve", END: END},
    )
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        decide,
        {"generate": "generate", "rewrite": "rewrite"},
    )
    graph.add_conditional_edges(
        "rewrite",
        decide_after_rewrite,
        {"agent": "agent", "insufficient": "insufficient"},
    )
    graph.add_edge("generate", END)
    graph.add_edge("insufficient", END)
    return graph.compile(checkpointer=checkpointer)


class Graph2State(TypedDict, total=False):
    question: str
    query: str
    documents: list
    evidence: list
    accepted_count: int
    retrieved_count: int
    generation: str
    result: dict
    transform_count: int
    generations: int
    allow_web: bool
    web_used: bool
    route: str
    trace: list
    queries: list
    history: list


def build_reference_graph2(
    *,
    retrieve,
    expand,
    answering,
    judge,
    web,
    allow_web: bool,
    max_rewrites: int,
    max_generations: int,
    checkpointer=None,
):
    def route_question(state):
        query = state["question"]
        if state.get("history"):
            query = judge.rewrite(query, query, state["history"][-6:])
        target = judge.route(query, allow_web, "graph2")
        if target == "web" and not allow_web:
            target = "retrieve"
        if target == "direct":
            target = "retrieve"
        return {
            "query": query,
            "route": target,
            **_event(state, "route_question", target=target),
        }

    def retrieve_node(state):
        hits, count, queries = retrieve(state.get("query", state["question"]))
        return {
            "documents": hits,
            "evidence": [],
            "queries": queries,
            "retrieved_count": state.get("retrieved_count", 0) + count,
            **_event(state, "retrieve", candidates=count, queries=queries),
        }

    def grade_documents(state):
        query = state.get("query", state["question"])
        relevant = [
            hit
            for hit in state["documents"]
            if judge.relevant(hit.get("matched_query", query), hit["entity"]["text"])
        ]
        evidence = expand(relevant) if relevant else []
        return {
            "documents": relevant,
            "evidence": evidence,
            "accepted_count": len(evidence),
            **_event(
                state,
                "grade_documents",
                relevant=len(relevant),
                evidence=len(evidence),
            ),
        }

    def decide_to_generate(state):
        if state.get("evidence"):
            return "generate"
        if state.get("transform_count", 0) < max_rewrites:
            return "transform_query"
        if allow_web and not state.get("web_used"):
            return "web_search"
        return "insufficient"

    def transform_query(state):
        query = judge.rewrite(
            state["question"],
            state.get("query", state["question"]),
            [],
        )
        return {
            "query": query,
            "transform_count": state.get("transform_count", 0) + 1,
            **_event(state, "transform_query", attempt=state.get("transform_count", 0) + 1),
        }

    def web_search(state):
        hits = web.search(state.get("query", state["question"]))
        evidence = [
            hit for hit in hits if judge.relevant(state["question"], hit["entity"]["text"])
        ]
        return {
            "documents": hits,
            "evidence": evidence,
            "accepted_count": len(evidence),
            "web_used": True,
            **_event(state, "web_search", results=len(evidence)),
        }

    def finish(state):
        history = state.get("history", [])
        result = state.get("result", {})
        if result.get("status") == "answered":
            history = (
                history
                + [{"question": state["question"], "answer": result["answer"][:2000]}]
            )[-6:]
        return {"history": history, **_event(state, "finish")}

    def generate(state):
        result = answering.answer(
            state["question"],
            state["evidence"],
            subquestions=state.get("queries", []),
        )
        result["status"] = "answered"
        return {
            "result": result,
            "generation": result["answer"],
            "generations": state.get("generations", 0) + 1,
            **_event(state, "generate", attempt=state.get("generations", 0) + 1),
        }

    def verify(state):
        result = state["result"]
        grounded = bool(result["citation_ids"]) and judge.grounded(state["question"], result)
        useful = grounded and judge.useful(
            state["question"],
            result["answer"],
            state.get("queries", []),
        )
        if grounded and useful:
            return {
                "decision": "finish",
                **_event(
                    state,
                    "verify",
                    grounded=True,
                    useful=True,
                    citations=result["citation_ids"],
                ),
            }
        if not grounded and state.get("generations", 0) < max_generations:
            return {
                "decision": "generate",
                **_event(
                    state,
                    "verify",
                    grounded=False,
                    citations=result["citation_ids"],
                ),
            }
        if (
            not useful
            and state.get("transform_count", 0) < max_rewrites
        ):
            return {
                "decision": "transform_query",
                **_event(
                    state,
                    "verify",
                    useful=False,
                    citations=result["citation_ids"],
                ),
            }
        if allow_web and not state.get("web_used"):
            return {"decision": "web_search", **_event(state, "verify", web_fallback=True)}
        return {"decision": "insufficient", **_event(state, "verify", exhausted=True)}

    def insufficient(state):
        return {
            "result": {
                "status": "insufficient_evidence",
                "answer": "在限定的检索、改写和生成次数内，未找到足以可靠回答的证据。",
                "citation_ids": [],
                "sources": [],
                "citation_check": "not_applicable",
            },
            **_event(state, "insufficient"),
        }

    graph = StateGraph(Graph2State)
    graph.add_node("route_question", route_question)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("transform_query", transform_query)
    graph.add_node("web_search", web_search)
    graph.add_node("generate", generate)
    graph.add_node("verify", verify)
    graph.add_node("insufficient", insufficient)
    graph.add_node("finish", finish)
    graph.add_edge(START, "route_question")
    graph.add_conditional_edges(
        "route_question",
        lambda state: state["route"],
        {"retrieve": "retrieve", "web": "web_search"},
    )
    graph.add_edge("web_search", "generate")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        decide_to_generate,
        {
            "generate": "generate",
            "transform_query": "transform_query",
            "web_search": "web_search",
            "insufficient": "insufficient",
        },
    )
    graph.add_edge("transform_query", "retrieve")
    graph.add_conditional_edges(
        "generate",
        lambda state: "verify",
        {"verify": "verify"},
    )
    graph.add_conditional_edges(
        "verify",
        lambda state: state["decision"],
        {
            "generate": "generate",
            "transform_query": "transform_query",
            "web_search": "web_search",
            "insufficient": "insufficient",
            "finish": "finish",
        },
    )
    graph.add_edge("insufficient", END)
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
