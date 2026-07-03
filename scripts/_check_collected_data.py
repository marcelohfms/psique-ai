import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONES = ["5581981464986", "5581985398370"]

FIELDS = [
    "stage", "user_name", "patient_name", "patient_age", "is_patient",
    "_is_patient_confirmed", "is_returning_patient", "preferred_doctor",
    "guardian_relationship", "birth_date", "guardian_name", "guardian_cpf",
    "patient_email", "patient_cpf", "consultation_reason",
    "referral_professional", "medication_note", "financial_name",
    "financial_cpf", "financial_email", "silent_mode", "pending_patients",
    "pending_confirmation_patient", "user_db_id", "modality_restriction",
    "age_exception", "pending_action", "pending_appointment",
]

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

    for phone in PHONES:
        config = {"configurable": {"thread_id": f"{phone}@s.whatsapp.net"}}
        print(f"\n==== {phone} ====")
        snap = await graph.aget_state(config)
        if not snap.values:
            print("Nenhum checkpoint encontrado.")
            continue
        for f in FIELDS:
            v = snap.values.get(f, "<ausente>")
            if v is not None and v != "<ausente>":
                print(f"  {f}: {v}")
        print(f"  -- mensagens: {len(snap.values.get('messages', []))}")

    await pg_conn.close()

asyncio.run(main())
