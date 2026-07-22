import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999656460@s.whatsapp.net"


async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import ToolMessage
    import psycopg, os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    conn = await psycopg.AsyncConnection.connect(conn_str, autocommit=True)
    conn.prepare_threshold = None  # avoid prepared-statement reuse issues through pooler
    async with conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": PHONE}}

        snapshot = await graph.aget_state(config)
        msgs = snapshot.values.get("messages") or []
        last_ai = next((m for m in reversed(msgs) if getattr(m, "type", None) == "ai"), None)
        invalid_calls = getattr(last_ai, "invalid_tool_calls", []) if last_ai else []
        answered_ids = {getattr(m, "tool_call_id", None) for m in msgs if getattr(m, "type", None) == "tool"}
        orphaned = [c for c in invalid_calls if c["id"] not in answered_ids]

        if not orphaned:
            print("Nada para reparar.")
            return

        error_msgs = [
            ToolMessage(content="Erro interno — tente novamente.", tool_call_id=c["id"])
            for c in orphaned
        ]
        await graph.aupdate_state(config, {"messages": error_msgs}, as_node="patient_agent")
        print(f"OK: checkpoint reparado — {[c['id'] for c in orphaned]}")

        # verify
        snapshot2 = await graph.aget_state(config)
        print("next:", snapshot2.next)


asyncio.run(main())
