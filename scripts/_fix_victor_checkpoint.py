import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581995106914@s.whatsapp.net"

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
    config = {"configurable": {"thread_id": PHONE}}

    before = await graph.aget_state(config)
    print("antes: is_returning_patient =", before.values.get("is_returning_patient"))

    await graph.aupdate_state(config, {"is_returning_patient": False})

    after = await graph.aget_state(config)
    print("depois: is_returning_patient =", after.values.get("is_returning_patient"))

    await pg_conn.close()

asyncio.run(main())
