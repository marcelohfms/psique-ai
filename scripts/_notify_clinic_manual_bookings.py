import asyncio
from dotenv import load_dotenv
load_dotenv()

# (número, appointment_id, data/hora legível, médico, modalidade, observação)
BOOKINGS = [
    ("5581997170891", "np3ju0jv37a1imk8b8fddrjbo4", "19/06/2026 às 09:00", "Dra. Bruna", "Presencial", "taxa de reserva pendente"),
    ("5581998208168", "k0tevtnhln54cf1j3qjlc4nm50", "01/07/2026 às 11:00", "Dr. Júlio", "Presencial", "taxa de reserva pendente"),
    ("5581998548664", "lqgik5sjc0dvl3409cdaon8l3o", "22/06/2026 às 16:30", "Dra. Bruna", "Online", "taxa de reserva pendente"),
    ("5581988851971", "ekuthn6utlp2m1rp2hpumb0cd0", "26/06/2026 às 10:00", "Dra. Bruna", "Presencial", "consulta gratuita"),
    ("5581992933054", "fi8j8aiclqqa8jo11432su5vh0", "06/07/2026 às 09:00", "Dr. Júlio", "Presencial", "taxa de reserva pendente"),
    ("5581999502366", "92m2fd9c0i9ohjsjr2l124b3vo", "01/07/2026 às 13:20", "Dra. Bruna", "Presencial", "cortesia (custo zero apenas nesta consulta)"),
]


def _fmt(v):
    return v if v not in (None, "", "não informado") else "—"


async def main():
    from app.database import get_supabase, get_user_by_phone
    from app.email_sender import send_clinic_notification_email

    client = await get_supabase()

    for number, appt_id, when, doctor, modality, obs in BOOKINGS:
        # find the user row that owns this appointment (correct patient when contact has many)
        appt = await client.from_("appointments").select("user_id").eq("appointment_id", appt_id).single().execute()
        uid = appt.data["user_id"] if appt.data else None
        u = None
        if uid:
            r = await client.from_("users").select("*").eq("id", uid).single().execute()
            u = r.data
        if not u:
            u = await get_user_by_phone(number)
        if not u:
            print(f"[SKIP] {number} {appt_id} — usuário não encontrado")
            continue

        contact = u.get("name") or ""
        patient = u.get("patient_name") or contact
        lines = ["📋 CADASTRO DO PACIENTE:"]
        if u.get("is_patient") is False and contact and contact != patient:
            lines.append(f"  Responsável/contato: {contact}")
        lines.append(f"  Nome: {_fmt(patient)}")
        lines.append(f"  Telefone: {number}")
        lines.append(f"  Idade: {_fmt(u.get('age'))}")
        lines.append(f"  Data de nascimento: {_fmt(u.get('birth_date'))}")
        lines.append(f"  CPF paciente: {_fmt(u.get('patient_cpf'))}")
        lines.append(f"  E-mail: {_fmt(u.get('email'))}")
        if u.get("guardian_name"):
            lines.append(f"  Responsável legal: {u.get('guardian_name')}")
        if u.get("guardian_cpf"):
            lines.append(f"  CPF responsável: {u.get('guardian_cpf')}")
        registration_block = "\n".join(lines)

        email_dest = _fmt(u.get("email"))
        body = (
            f"Agendamento realizado! ✅\n"
            f"Paciente: {patient}\n"
            f"Data e horário: {when}\n"
            f"Médico(a): {doctor}\n"
            f"Modalidade: {modality}\n"
            f"Observação: {obs}\n\n"
            f"📋 LEMBRETE: enviar o Termo de Compromisso para o e-mail do paciente ({email_dest}).\n\n"
            f"{registration_block}"
        )
        subject = f"Agendamento realizado — {patient}"
        await send_clinic_notification_email(subject, body)
        print(f"[OK] e-mail enviado: {patient} | {when} | {doctor}")


asyncio.run(main())
