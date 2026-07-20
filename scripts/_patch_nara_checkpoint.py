import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996571022"

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

    patch = {
        "is_returning_patient": True,
    }

    await graph.aupdate_state(config, patch, as_node="collect_info")
    print("Checkpoint atualizado: is_returning_patient=True.")
    await pg_conn.close()

asyncio.run(main())
