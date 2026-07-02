import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Detalhes do agendamento cancelado
    appt = await client.from_("appointments").select("*") \
        .eq("appointment_id", "abcmv3s0lbtgf8oe8cvp0bu1mg").single().execute()
    a = appt.data
    print("=== AGENDAMENTO ===")
    for k, v in a.items():
        if v is not None:
            print(f"  {k}: {v}")

    # Mensagens recentes
    print("\n=== MENSAGENS RECENTES ===")
    for phone in ["5587981054742", "5587981054742@s.whatsapp.net"]:
        msgs = await client.from_("messages").select("role, content, created_at") \
            .eq("phone", phone).order("created_at", desc=True).limit(20).execute()
        if msgs.data:
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                content = (m["content"] or "")[:200].replace("\n", " ")
                print(f"  {ts} [{m['role']:9}] {content}")
            break

asyncio.run(main())
