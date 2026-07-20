import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581998653907"
PATIENT_ID = "8debbb77-4f59-4026-ab56-26acbbe4f9dc"

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
        "user_db_id": PATIENT_ID,
        "patient_cpf": "023.861.574-07",
        "financial_name": "Leonardo Felix Nascimento",
        "financial_cpf": "155.007.514-41",
        "financial_email": "leonardopernambuco@hotmail.com",
    }

    await graph.aupdate_state(config, patch, as_node="collect_info")
    print("Checkpoint atualizado com sucesso.")
    await pg_conn.close()

asyncio.run(main())
