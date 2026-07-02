import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    PHONE = "5581995302944"
    for phone_fmt in [PHONE, f"{PHONE}@s.whatsapp.net"]:
        r = await client.from_("events").select("event_type, phone, data, created_at") \
            .ilike("phone", f"%{PHONE[-9:]}%").order("created_at", desc=True).limit(20).execute()
        if r.data:
            print(f"Eventos:")
            for e in r.data:
                ts = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                print(f"  {ts} | {e['event_type']} | {str(e.get('data',''))[:150]}")
            break
    else:
        print("Sem eventos")

asyncio.run(main())
