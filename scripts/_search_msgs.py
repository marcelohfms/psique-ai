import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Busca mensagens do assistente que contenham "Felipe"
    result = await client.from_("messages") \
        .select("phone, role, content, created_at") \
        .eq("role", "assistant") \
        .ilike("content", "%Felipe%") \
        .order("created_at", desc=True) \
        .limit(20) \
        .execute()

    for m in result.data:
        ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        content = (m["content"] or "")[:200].replace("\n", " ")
        print(f"{ts} | {m['phone']} | {content}")

asyncio.run(main())
