import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg
    import os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        thread = "5581999656460@s.whatsapp.net"
        config = {"configurable": {"thread_id": thread}}
        snapshot = await graph.aget_state(config)
        msgs = snapshot.values.get("messages") or []
        for i, m in enumerate(msgs):
            print(i, type(m).__name__, repr(m)[:600])
            print()

asyncio.run(main())
