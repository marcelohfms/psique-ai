import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    client = await get_supabase()
    # Busca pela variante com @s.whatsapp.net também
    for phone in ["5581999995736", "5581999995736@s.whatsapp.net"]:
        result = await client.from_("messages") \
            .select("role, content, created_at") \
            .eq("phone", phone) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()
        if result.data:
            print(f"=== {phone} ===")
            for m in reversed(result.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                role = "👤" if m["role"] == "human" else "🤖"
                content = (m["content"] or "")[:150].replace("\n", " ")
                print(f"{ts} {role} {content}")

asyncio.run(main())
