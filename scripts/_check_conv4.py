import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    client = await get_supabase()
    # Busca pelo user_id da Sayonara
    users = await client.from_("users").select("id, number, name").ilike("name", "%sayonara%").execute()
    print("Users:", users.data)
    
    for u in users.data:
        uid = u["id"]
        # tenta buscar mensagens pelo número completo
        number = u.get("number", "")
        print(f"\nBuscando mensagens para number={number}")
        msgs = await client.from_("messages").select("phone, role, content, created_at") \
            .ilike("phone", f"%{number[-9:]}%").order("created_at", desc=True).limit(30).execute()
        if msgs.data:
            phones = set(m["phone"] for m in msgs.data)
            print(f"  Phones encontrados: {phones}")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                role = "👤" if m["role"] == "human" else "🤖"
                content = (m["content"] or "")[:200].replace("\n", " ")
                print(f"  {ts} {role} {content}")
        else:
            print("  Sem mensagens")

asyncio.run(main())
