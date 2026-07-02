import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997640726"
CONTACT_NAME = "Andreza Guimarães Brandão"
PATIENT_NAME = "Bernardo Guimarães de Nóbrega"
PATIENT_AGE = 10
BIRTH_DATE = "22/07/2015"
PATIENT_EMAIL = "andrezaguimaraesbrandao@gmail.com"
PATIENT_CPF = "72075475440"
PATIENT_ID = "87a6532c-2191-4203-a3ff-01b65af57ea7"
CONTACT_ID = "7ee8e412-f756-4a74-8c0b-a466534be355"

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
        "user_name": CONTACT_NAME,
        "patient_name": PATIENT_NAME,
        "patient_age": PATIENT_AGE,
        "birth_date": BIRTH_DATE,
        "patient_email": PATIENT_EMAIL,
        "patient_cpf": PATIENT_CPF,
        "is_patient": False,
        "is_new_patient": True,
        "stage": "patient_agent",
        "_contact_id": CONTACT_ID,
        "_patient_id": PATIENT_ID,
    }

    await graph.aupdate_state(config, patch, as_node="collect_info")
    print("Checkpoint atualizado com sucesso.")
    await pg_conn.close()

asyncio.run(main())
