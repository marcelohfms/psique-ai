import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996465000"

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
    config = {"configurable": {"thread_id": f"whatsapp_{PHONE}"}}

    patch = {
        "user_name": "Maria de Fátima Costa Oliveira Cavalcanti de Albuquerque",
        "patient_name": "Maria de Fátima Costa Oliveira Cavalcanti de Albuquerque",
        "patient_age": 72,
        "birth_date": "13/03/1954",
        "patient_email": "fatima_rec@yahoo.com.br",
        "patient_cpf": "138554924-68",
        "is_patient": True,
        "is_new_patient": True,
        "stage": "patient_agent",
        "_contact_id": "4a89d0a4-dac4-4b79-9a90-29d1e694ccdb",
        "_patient_id": "112b6bb3-f50d-4840-b9bf-b5dcc524bac0",
    }

    await graph.aupdate_state(config, patch, as_node="collect_info")
    print("Checkpoint atualizado.")
    await pg_conn.close()

asyncio.run(main())
