import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    PHONE = "5581995302944"
    users = await get_users_by_phone(f"{PHONE}@s.whatsapp.net")
    print(f"Usuários:")
    for u in users:
        print(f"  {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')} | doctor_id={u.get('doctor_id')}")

    client = await get_supabase()
    for phone_fmt in [PHONE, f"{PHONE}@s.whatsapp.net"]:
        msgs = await client.from_("messages").select("role, content, created_at") \
            .eq("phone", phone_fmt).order("created_at", desc=True).limit(40).execute()
        if msgs.data:
            print(f"\n=== Mensagens ({phone_fmt}) ===")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                role = "👤" if m["role"] == "human" else "🤖"
                content = (m["content"] or "")[:200].replace("\n", " ")
                print(f"{ts} {role} {content}")

asyncio.run(main())
