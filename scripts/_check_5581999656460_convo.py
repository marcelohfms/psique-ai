import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # phone variants: with/without extra 9, with whatsapp suffix
    phones = [
        "5581999656460",
        "558199656460",
        "5581999656460@s.whatsapp.net",
    ]

    for phone in phones:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:400]}")

    # contacts / patients lookup
    for phone in phones:
        c = await client.from_("contacts").select("*").eq("phone", phone).execute()
        if c.data:
            print(f"\n=== contacts phone={phone} ===")
            for row in c.data:
                print(row)

asyncio.run(main())
