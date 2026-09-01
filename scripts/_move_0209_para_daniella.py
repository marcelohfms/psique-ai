import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "uvujd8pjg6aha3a7h6rfegnvmc"
BENTO = "161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"
DANIELLA = "970df18e-268c-4454-b3ce-dc50882c9c6b"
DANIELLA_PHONE = "5581991749847"

async def show(c, tag):
    ap = await c.from_("appointments").select(
        "appointment_id,patient_id,contact_id,status,start_time,booking_fee_paid_at,confirmed_at,patients(name)"
    ).eq("appointment_id", APPT).execute()
    a = (ap.data or [None])[0]
    print(f"[{tag}] patient_id={a['patient_id']} nome={a.get('patients',{}).get('name') if a.get('patients') else '?'} "
          f"contact_id={a['contact_id']} status={a['status']} paid={a['booking_fee_paid_at']}")

async def main():
    from app.supabase_client import get_supabase
    from app.patients import get_reminder_contacts
    from app.database import log_event
    c = await get_supabase()

    await show(c, "ANTES")
    before = await get_reminder_contacts(BENTO, "consulta", include_inactive=True)
    print(f"  lembrete ANTES (ficha Bento) -> {[(x.get('phone'), x.get('name')) for x in before]}")

    # UPDATE mesma linha: so troca o dono da ficha. booking_fee_paid_at/confirmed_at preservados.
    await c.from_("appointments").update({"patient_id": DANIELLA}).eq("appointment_id", APPT).execute()

    await show(c, "DEPOIS")
    after = await get_reminder_contacts(DANIELLA, "consulta", include_inactive=True)
    print(f"  lembrete DEPOIS (ficha Daniella) -> {[(x.get('phone'), x.get('name')) for x in after]}")

    await log_event(
        DANIELLA_PHONE,
        "attendant_appointment_ficha_moved",
        {
            "appointment_id": APPT,
            "old_patient_id": BENTO,
            "new_patient_id": DANIELLA,
            "reason": "orientacao_de_pais_so_com_a_mae; sessao caiu na ficha do menor ao remarcar; movida p/ ficha propria da Daniella p/ nao notificar o pai (Sandro)",
        },
    )
    print("evento de auditoria registrado.")

asyncio.run(main())
