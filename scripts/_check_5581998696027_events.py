import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()
    phones = _phone_variants("5581998696027")
    print("variants:", phones)
    try:
        res = await client.from_("events").select("*").in_("phone", phones).order("created_at", desc=True).limit(20).execute()
        print(f"events: {len(res.data)}")
        for e in res.data:
            dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {e.get('event_type')} | {str(e.get('payload'))[:200]}")
    except Exception as ex:
        print("events query failed:", ex)

asyncio.run(main())
