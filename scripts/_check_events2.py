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
    # descobre colunas da tabela events
    r = await client.from_("events").select("*").limit(1).execute()
    if r.data:
        print("Colunas events:", list(r.data[0].keys()))

    # busca por phone
    r2 = await client.from_("events").select("*").ilike("phone", f"%302944%").order("created_at", desc=True).limit(20).execute()
    for e in r2.data:
        ts = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"  {ts} | {e.get('event_type')} | {str(e)[:200]}")

asyncio.run(main())
