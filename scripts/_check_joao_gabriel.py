import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Busca o cadastro
    r = await client.from_("users").select("id, number, name, patient_name, is_patient, doctor_id") \
        .ilike("patient_name", "%joão gabriel%").execute()
    r2 = await client.from_("users").select("id, number, name, patient_name, is_patient, doctor_id") \
        .ilike("name", "%joão gabriel%").execute()
    users = {u["id"]: u for u in r.data + r2.data}.values()
    for u in users:
        print(f"id={u['id']} | number={u['number']} | name={u.get('name')} | patient_name={u.get('patient_name')} | is_patient={u.get('is_patient')}")

    # Mensagens recentes
    for u in users:
        phone = u["number"]
        msgs = await client.from_("messages").select("role, content, created_at") \
            .eq("phone", phone).order("created_at", desc=True).limit(15).execute()
        if msgs.data:
            print(f"\n=== Msgs {phone} ===")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                content = (m["content"] or "")[:250].replace("\n", " ")
                print(f"  {ts} [{m['role']:9}] {content}")

asyncio.run(main())
