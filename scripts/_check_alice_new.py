import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("5587981054742@s.whatsapp.net")
    u = users[0]
    uid = u["id"]

    # Todos os agendamentos recentes
    appts = await client.from_("appointments").select(
        "appointment_id, start_time, status, booking_fee_paid_at, created_at"
    ).eq("user_id", uid).order("start_time", desc=True).limit(5).execute()
    print("=== AGENDAMENTOS ===")
    for a in appts.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        created = datetime.fromisoformat(a["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        taxa = "✅" if a.get("booking_fee_paid_at") else "⚠️"
        print(f"  {start} | {a['status']} | taxa={taxa} | criado={created} | {a['appointment_id']}")

    # Mensagens recentes
    print("\n=== MENSAGENS RECENTES ===")
    msgs = await client.from_("messages").select("role, content, created_at") \
        .eq("phone", "5587981054742").order("created_at", desc=True).limit(10).execute()
    for m in reversed(msgs.data):
        ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        content = (m["content"] or "")[:200].replace("\n", " ")
        print(f"  {ts} [{m['role']:9}] {content}")

asyncio.run(main())
