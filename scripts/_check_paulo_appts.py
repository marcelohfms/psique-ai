import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    appts = await client.from_("appointments").select(
        "appointment_id, start_time, status, consultation_type, modality"
    ).eq("user_id", "46de5fc6-fbce-46c7-b2e0-711d75babb04").order("start_time").execute()

    for a in appts.data:
        start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"  {start} | status={a['status']} | consultation_type={a.get('consultation_type')} | modality={a.get('modality')}")

asyncio.run(main())
