import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")

    users = await get_users_by_phone("558799880457@s.whatsapp.net")
    if not users:
        print("Nenhum usuário encontrado")
        return
    client = await get_supabase()
    for u in users:
        print(f"Paciente: {u.get('patient_name') or u.get('name')} | is_patient={u.get('is_patient')} | doctor={u.get('doctor_id')}")
        appts = await client.from_("appointments").select(
            "appointment_id, start_time, end_time, status, modality, booking_fee_paid_at, paid_at"
        ).eq("user_id", u["id"]).order("start_time", desc=True).limit(5).execute()
        for a in appts.data:
            start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            duration = int((datetime.fromisoformat(a["end_time"]) - datetime.fromisoformat(a["start_time"])).seconds / 60)
            taxa = "✅ paga" if a.get("booking_fee_paid_at") else "⚠️ pendente"
            consulta = "✅ paga" if a.get("paid_at") else "—"
            print(f"  {start} ({duration}min) | {a['status']} | {a.get('modality') or 'n/d'} | taxa={taxa} | consulta={consulta}")

asyncio.run(main())
