import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Verifica se já existe agendamento para Sayonara em 17/06
    user = await client.from_("users").select("id").eq("number", "5581995302944").single().execute()
    uid = user.data["id"]
    appts = await client.from_("appointments").select("appointment_id, start_time, status") \
        .eq("user_id", uid).order("start_time", desc=True).limit(5).execute()
    print(f"user_id={uid}")
    for a in appts.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"  {start} | {a['status']} | {a['appointment_id']}")

asyncio.run(main())
