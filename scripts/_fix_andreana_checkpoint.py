import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997006125@s.whatsapp.net"
NEW_PATIENT_ID = "d590cff4-84db-4c12-8afb-281f263ce633"

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
    print("antes:", {k: before.values.get(k) for k in (
        "user_db_id", "user_name", "patient_name", "patient_age",
        "birth_date", "patient_email", "is_returning_patient",
    )})

    await graph.aupdate_state(config, {
        "user_db_id": NEW_PATIENT_ID,
        "user_name": "Andreana Beserra Quidute Sole",
        "patient_name": "Andreana Beserra Quidute Sole",
        "patient_age": 61,
        "birth_date": "29/04/1965",
        "patient_email": "andreanaquidute@gmail.com",
        "is_returning_patient": True,
    })

    after = await graph.aget_state(config)
    print("depois:", {k: after.values.get(k) for k in (
        "user_db_id", "user_name", "patient_name", "patient_age",
        "birth_date", "patient_email", "is_returning_patient",
    )})

    await pg_conn.close()

asyncio.run(main())
