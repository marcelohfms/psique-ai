import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996937559"

async def main():
    from app.database import get_supabase, get_users_by_phone, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    seen_ids = set()
    for phone in _phone_variants(PHONE):
        users = await get_users_by_phone(phone)
        print(f"\n=== variante {phone}: {len(users) if users else 0} registro(s) ===")
        if not users:
            continue
        for u in users:
            pid = u.get("id")
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            print(f"--- registro id={pid} ---")
            print(f"  name/patient_name: {u.get('name')} / {u.get('patient_name')}")
            print(f"  is_patient: {u.get('is_patient')} | is_returning_patient: {u.get('is_returning_patient')}")

            appts = await client.from_("appointments").select("*").eq("patient_id", pid).order("start_time", desc=True).limit(10).execute()
            if appts.data:
                print("  Consultas (patient_id):")
                for a in appts.data:
                    start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                    print(f"    {start} | status={a['status']} | modality={a.get('modality')} | consultation_type={a.get('consultation_type')} | booking_fee_paid_at={a.get('booking_fee_paid_at')} | booking_fee_waived={a.get('booking_fee_waived')} | id={a['id']}")
            else:
                print("  Nenhuma consulta encontrada (patient_id)")

    print("\n=== checkpoint (LangGraph state) ===")
    import os
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
        "preferred_doctor", "stage", "reschedule_in_progress",
        "pending_confirmation", "reschedule_initiated_by",
    ]
    for k in keys_of_interest:
        print(f"{k}: {snap.values.get(k)!r}")

    print("\nÚltimas mensagens:")
    for m in snap.values.get("messages", [])[-15:]:
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = str(content)
        print(f"  {type(m).__name__}: {str(content)[:300]}")

    await pg_conn.close()

asyncio.run(main())
