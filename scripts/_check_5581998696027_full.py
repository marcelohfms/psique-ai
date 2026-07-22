import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, _phone_variants, get_user_by_phone, DOCTOR_NAMES
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Full content of the receipt message
    for phone in _phone_variants("5581998696027"):
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(6).execute()
        for m in reversed(msgs.data):
            if "[imagem]" in (m.get("content") or "") or "COMPROVANTE" in (m.get("content") or ""):
                dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
                print(f"--- {phone} {dt} {m.get('role')} ---")
                print(m.get("content"))
                print()

    # Patient + appointment state
    user = await get_user_by_phone("5581998696027@s.whatsapp.net")
    print("=== USER ===")
    if user:
        print("patient_name:", user.get("patient_name") or user.get("name"))
        print("user_id:", user["id"], "doctor:", DOCTOR_NAMES.get(user.get("doctor_id",""),""))
        uid = user["id"]
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, status, paid_at, consultation_type"
        ).eq("user_id", uid).order("start_time", desc=True).limit(6).execute()
        print("=== APPOINTMENTS ===")
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  {a['appointment_id']} | {st} | status={a['status']} | paid_at={a.get('paid_at')} | type={a.get('consultation_type')}")
    else:
        print("Usuário não encontrado")

asyncio.run(main())
