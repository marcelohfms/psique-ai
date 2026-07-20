import asyncio
from dotenv import load_dotenv
load_dotenv()

THREADS = [
    "558179087152@s.whatsapp.net",
    "558186651067@s.whatsapp.net",
    "558191183875@s.whatsapp.net",
    "5581992717880@s.whatsapp.net",
    "558199687066@s.whatsapp.net",
]


async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import ToolMessage
    import psycopg, os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    conn = await psycopg.AsyncConnection.connect(conn_str, autocommit=True)
    conn.prepare_threshold = None
    async with conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        for thread in THREADS:
            config = {"configurable": {"thread_id": thread}}
            snapshot = await graph.aget_state(config)
            msgs = snapshot.values.get("messages") or []
            last_ai = next((m for m in reversed(msgs) if getattr(m, "type", None) == "ai"), None)
            pending = (getattr(last_ai, "tool_calls", []) or []) + (getattr(last_ai, "invalid_tool_calls", []) or [])
            answered_ids = {getattr(m, "tool_call_id", None) for m in msgs if getattr(m, "type", None) == "tool"}
            orphaned = [c for c in pending if c["id"] not in answered_ids]
            if not orphaned:
                print(f"{thread}: nada para reparar (já resolvido)")
                continue
            error_msgs = [ToolMessage(content="Erro interno — tente novamente.", tool_call_id=c["id"]) for c in orphaned]
            await graph.aupdate_state(config, {"messages": error_msgs}, as_node="patient_agent")
            print(f"{thread}: reparado — {[c['id'] for c in orphaned]}")


asyncio.run(main())
