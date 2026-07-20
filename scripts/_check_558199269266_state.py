import asyncio, os
from dotenv import load_dotenv
load_dotenv()

RAW = "558199269266"
# variantes com/sem 9
VARIANTS = [
    "558199269266",
    "5581999269266",
    "5581992692266",
]

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # busca por sufixo de 8 digitos
    suffix = RAW[-8:]
    print(f"=== buscando mensagens por sufixo {suffix} ===")
    msgs = await client.from_("messages").select("phone,role,content,created_at").ilike("phone", f"%{suffix}").order("created_at", desc=False).execute()
    print(f"{len(msgs.data)} mensagens")
    phones = set()
    for m in msgs.data:
        phones.add(m["phone"])
        dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        print(f"  {dt} | {m['phone']} | {m.get('role')} | {(m.get('content') or '')[:300]}")

    print(f"\nphones encontrados: {phones}")

asyncio.run(main())
