import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from app.phone import _strip_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    raw = "5581994358739"
    stripped = _strip_phone(raw)
    print(f"raw={raw} stripped={stripped}")

    for phone in {raw, stripped, f"{raw}@s.whatsapp.net"}:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=False).execute()
        print(f"\n=== phone={phone}: {len(msgs.data)} mensagens ===")
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {m.get('content', '')[:300]}")

    from app.database import get_users_by_phone
    users = await get_users_by_phone(stripped)
    print(f"\n=== users row(s) for phone={stripped} ===")
    for u in users:
        print(u)

asyncio.run(main())
