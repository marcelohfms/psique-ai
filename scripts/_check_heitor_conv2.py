import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    msgs = await client.from_("messages").select("role, content, created_at") \
        .ilike("phone", "%996001122%").order("created_at", desc=True).limit(20).execute()
    if msgs.data:
        for m in reversed(msgs.data):
            ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
            content = (m["content"] or "")[:250].replace("\n", " ")
            print(f"  {ts} [{m['role']:9}] {content}")
    else:
        print("Sem mensagens")

asyncio.run(main())
