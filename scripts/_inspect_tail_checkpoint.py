import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581994344760"

async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph.graph import build_graph

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    pg_conn = await AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row
    )
    checkpointer = AsyncPostgresSaver(pg_conn)
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"{PHONE}@s.whatsapp.net"}}

    snap = await graph.aget_state(config)
    msgs = snap.values.get("messages", [])
    print("total:", len(msgs))
    for i, m in enumerate(msgs[-15:], start=len(msgs)-15):
        tc = getattr(m, "tool_calls", None)
        print(f"{i}: {type(m).__name__} tool_calls={tc} content={str(getattr(m,'content',''))[:150]}")

    print("\nstage:", snap.values.get("stage"))
    print("silent_mode:", snap.values.get("silent_mode"))

    await pg_conn.close()

asyncio.run(main())
