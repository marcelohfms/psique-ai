import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996937559"

async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph.graph import build_graph

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    pg_conn = await AsyncConnection.connect(conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row)
    checkpointer = AsyncPostgresSaver(pg_conn)
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"{PHONE}@s.whatsapp.net"}}
    snap = await graph.aget_state(config)
    for k in ("user_db_id", "user_id", "patient_id", "silent_mode", "reschedule_in_progress", "reschedule_initiated_by", "pending_confirmation"):
        print(f"{k}: {snap.values.get(k)!r}")
    await pg_conn.close()

asyncio.run(main())
