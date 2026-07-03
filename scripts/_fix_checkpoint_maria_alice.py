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

    patch = {
        "user_name": "Marcela Cavalcanti Cabral de Mello",
        "is_patient": False,
        "_is_patient_confirmed": True,
        "patient_name": "Maria Alice Cavalcanti Cabral De Mello",
        "birth_date": "07/08/2008",
        "patient_age": 17,
        "is_returning_patient": True,
        "patient_cpf": "115.385.584-40",
        "guardian_name": "Rodolfo Guilherme Fernandes Mattos",
        "guardian_cpf": "047.655.554-02",
        "preferred_doctor": "julio",
        "patient_email": "marcelac724@gmail.com",
        "stage": "patient_agent",
        "_contact_id": "a5990314-9081-4da4-ba17-6c7f27322e79",
        "_patient_id": "6f9970c4-22ba-42b2-8c0a-d5837fc2390e",
    }

    await graph.aupdate_state(config, patch, as_node="collect_info")
    print("Checkpoint atualizado com sucesso.")
    await pg_conn.close()

asyncio.run(main())
