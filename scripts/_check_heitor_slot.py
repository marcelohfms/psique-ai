import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    calendar_id = await _get_doctor_calendar_id("bruna")
    slots = await get_available_slots(calendar_id, "17/06", "tarde", 60, "bruna")
    print("Slots disponíveis 17/06 tarde (Dra. Bruna):")
    for s, m in slots:
        print(f"  {s.astimezone(TZ).strftime('%H:%M')} [{m}]")

    # Extrai drive_link do comprovante na conversa
    from app.database import get_supabase
    import re
    client = await get_supabase()
    msgs = await client.from_("messages").select("content") \
        .eq("phone", "558196001122").ilike("content", "%drive_link%").execute()
    for m in msgs.data:
        links = re.findall(r'drive_link:(https?://[^\]]+)', m["content"])
        if links:
            print(f"\nDrive link: {links[0]}")

asyncio.run(main())
