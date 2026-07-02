import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    for phone in ["5581991520435", "5581991520435@s.whatsapp.net"]:
        msgs = await client.from_("messages").select("role, content, created_at") \
            .eq("phone", phone).order("created_at", desc=True).limit(30).execute()
        if msgs.data:
            print(f"=== {phone} ===")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                content = (m["content"] or "")[:300].replace("\n", " ")
                print(f"  {ts} [{m['role']:9}] {content}")
            break

asyncio.run(main())
