import asyncio, re
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone("5581981963813@s.whatsapp.net")
    for u in users:
        print(f"name={u.get('name')} | patient_name={u.get('patient_name')} | doctor_id={u.get('doctor_id')} | id={u['id']}")

    # Drive link na conversa
    msgs = await client.from_("messages").select("content, created_at") \
        .eq("phone", "5581981963813").ilike("content", "%drive_link%") \
        .order("created_at", desc=True).limit(3).execute()
    for m in msgs.data:
        links = re.findall(r'drive_link:(https?://[^\]]+)', m["content"])
        print(f"Drive link: {links[0] if links else 'não encontrado'}")
        print(f"Conteúdo: {m['content'][:300]}")

asyncio.run(main())
