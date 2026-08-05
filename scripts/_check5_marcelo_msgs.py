import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999865181"


async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    m = await client.from_("messages").select("*").eq("phone", PHONE).order("created_at").execute()
    print(f"total mensagens: {len(m.data)}\n")
    for x in m.data:
        ts = datetime.fromisoformat(x["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        role = x.get("role") or x.get("direction") or "?"
        body = (x.get("content") or x.get("body") or "").replace("\n", " ")[:300]
        print(f"[{ts}] {role}: {body}")


asyncio.run(main())
