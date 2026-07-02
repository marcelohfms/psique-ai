import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    client = await get_supabase()
    # tenta variantes do número
    for phone_fmt in ["5581995302944", "5581995302944@s.whatsapp.net", "5581995302944"]:
        msgs = await client.from_("messages").select("role, content, created_at") \
            .eq("phone", phone_fmt).order("created_at", desc=True).limit(50).execute()
        if msgs.data:
            print(f"\n=== {phone_fmt} ({len(msgs.data)} msgs) ===")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                role = "👤" if m["role"] == "human" else "🤖"
                content = (m["content"] or "")[:250].replace("\n", " ")
                print(f"{ts} {role} {content}")
        else:
            print(f"{phone_fmt}: sem mensagens")

asyncio.run(main())
