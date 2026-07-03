import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    client = await get_supabase()
    # busca mensagens por ilike no phone
    msgs = await client.from_("messages").select("phone, role, content, created_at") \
        .ilike("phone", "%995302944%").order("created_at", desc=True).limit(50).execute()
    
    if msgs.data:
        print(f"{len(msgs.data)} mensagens encontradas")
        # agrupa por phone
        phones = set(m["phone"] for m in msgs.data)
        print("Phones encontrados:", phones)
        for m in reversed(msgs.data):
            ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
            role = "👤" if m["role"] == "human" else "🤖"
            content = (m["content"] or "")[:250].replace("\n", " ")
            print(f"{ts} {role} {content}")
    else:
        print("Nenhuma mensagem encontrada")

asyncio.run(main())
