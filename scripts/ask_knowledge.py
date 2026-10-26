"""CLI client for the same API/workflow used by the browser."""

import argparse
from uuid import UUID, uuid4

import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.add_argument("--knowledge-base-id", type=UUID, required=True)
    p.add_argument("--document-id", type=UUID)
    p.add_argument(
        "--mode",
        choices=["classic", "agent", "adaptive", "graph1", "graph2"],
        default="adaptive",
    )
    p.add_argument("--allow-web", action="store_true")
    args = p.parse_args()
    url = f"{args.base_url.rstrip('/')}/api/v1/knowledge-bases/{args.knowledge_base_id}"
    if args.document_id:
        url += f"/documents/{args.document_id}"
    session_id = str(uuid4())
    with httpx.Client(timeout=1800) as client:
        while True:
            try:
                question = input("问题（exit 退出，/new 新会话）：").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in {"exit", "quit", "q"}:
                break
            if question == "/new":
                session_id = str(uuid4())
                continue
            if not question:
                continue
            response = client.post(
                url + "/ask",
                json={
                    "question": question,
                    "mode": args.mode,
                    "session_id": session_id,
                    "allow_web": args.allow_web,
                },
            )
            if response.is_error:
                print(response.status_code, response.text)
                continue
            result = response.json()
            print(" → ".join(x["node"] for x in result["trace"]))
            print(result["answer"])
            for source in result["sources"]:
                if source["source_id"] in result["citation_ids"]:
                    print(
                        source["source_id"],
                        source["filename"],
                        source.get("url") or source["page_number"],
                    )


if __name__ == "__main__":
    main()
