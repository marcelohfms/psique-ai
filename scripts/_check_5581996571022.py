import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONES = ["5581996571022@s.whatsapp.net", "558196571022@s.whatsapp.net"]


async def main():
    from app.database import get_users_by_phone, get_contact_by_phone, get_supabase

    client = await get_supabase()

    for phone in PHONES:
        users = await get_users_by_phone(phone)
        print(f"=== get_users_by_phone({phone}): {len(users)} ===")
        for u in users:
            print(f"  patient_id={u.get('id')} name={u.get('name')!r} is_returning_patient={u.get('is_returning_patient')!r} doctor_id={u.get('doctor_id')!r}")

    contact = None
    for phone in PHONES:
        contact = await get_contact_by_phone(phone)
        if contact:
            print(f"\n=== contact ({phone}) ===")
            for k, v in contact.items():
                print(f"  {k}: {v!r}")
            break

    if not contact:
        print("Nenhum contato encontrado para esses telefones.")
        return

    pc = await (
        client.from_("patient_contacts")
        .select("*, patients(*)")
        .eq("contact_id", contact["id"])
        .execute()
    )
    print(f"\n=== patient_contacts ({len(pc.data or [])}) ===")
    patient_ids = []
    for row in pc.data or []:
        patient = row.get("patients") or {}
        pid = patient.get("id")
        patient_ids.append(pid)
        print(f"  patient_id={pid} name={patient.get('name')!r} is_self={row.get('is_self')} relationship={row.get('relationship')} role={row.get('role')} is_returning_patient={patient.get('is_returning_patient')!r}")

    for pid in patient_ids:
        if not pid:
            continue
        appts = await (
            client.from_("appointments")
            .select("id, appointment_id, status, consultation_type, start_time, end_time, confirmed_at, pos_consulta_sent_at")
            .eq("patient_id", pid)
            .order("start_time")
            .execute()
        )
        print(f"\n=== appointments for patient_id={pid} ({len(appts.data or [])}) ===")
        for a in appts.data or []:
            print(f"  {a}")


asyncio.run(main())
