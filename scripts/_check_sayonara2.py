import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Mensagens recentes
    print("=== MENSAGENS RECENTES ===")
    msgs = await client.from_("messages").select("role, content, created_at") \
        .eq("phone", "558195302944").order("created_at", desc=True).limit(20).execute()
    for m in reversed(msgs.data):
        ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        content = (m["content"] or "")[:250].replace("\n", " ")
        print(f"  {ts} [{m['role']:9}] {content}")

    # Agendamentos no slot 17/06 11h com Dra. Bruna
    print("\n=== AGENDAMENTOS 17/06 11:00 (Dra. Bruna) ===")
    BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"
    appts = await client.from_("appointments").select(
        "appointment_id, user_id, start_time, status, booking_fee_paid_at, users(name, patient_name)"
    ).eq("doctor_id", BRUNA_ID).gte("start_time", "2026-06-17T14:00:00").lte("start_time", "2026-06-17T14:01:00").execute()
    for a in appts.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m %H:%M")
        user = a.get("users") or {}
        print(f"  {start} | status={a['status']} | user={user.get('patient_name') or user.get('name')} | booking_fee={a.get('booking_fee_paid_at')} | id={a['appointment_id']}")

    # Todos agendamentos da Sayonara
    print("\n=== AGENDAMENTOS SAYONARA ===")
    user = await client.from_("users").select("id").eq("number", "5581995302944").single().execute()
    uid = user.data["id"]
    appts2 = await client.from_("appointments").select("appointment_id, start_time, status, booking_fee_paid_at") \
        .eq("user_id", uid).order("start_time", desc=True).limit(5).execute()
    for a in appts2.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"  {start} | status={a['status']} | booking_fee={a.get('booking_fee_paid_at')} | id={a['appointment_id']}")

asyncio.run(main())
