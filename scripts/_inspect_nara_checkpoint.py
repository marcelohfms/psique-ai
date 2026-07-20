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

    snap = await graph.aget_state(config)

    keys_of_interest = [
        "user_name", "is_patient", "_is_patient_confirmed", "patient_name",
        "birth_date", "is_returning_patient", "patient_cpf", "patient_age",
        "guardian_name", "guardian_cpf", "preferred_doctor", "patient_email", "stage",
    ]
    for k in keys_of_interest:
        print(f"{k}: {snap.values.get(k)!r}")

    print("\nÚltimas mensagens:")
    for m in snap.values.get("messages", [])[-10:]:
        print(f"  {type(m).__name__}: {str(getattr(m, 'content', ''))[:150]}")

    await pg_conn.close()

asyncio.run(main())
