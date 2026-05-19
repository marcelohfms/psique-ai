"""
Send each doctor their appointment list for the next day via email.
Runs every day at 18h Recife time via GitHub Actions.

Queries tomorrow's scheduled appointments, groups by doctor, and sends
one email per doctor with the full list.
"""
import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}

MODALITY_LABELS = {
    "online": "Online",
    "presencial": "Presencial",
    "escolha": "Online/Presencial",
}


def _format_agenda_email(doctor_label: str, date_str: str, appointments: list) -> tuple[str, str]:
    """Return (subject, body) for the doctor's daily agenda email."""
    subject = f"Agenda de amanhã — {date_str} | {doctor_label}"

    lines = [
        f"{doctor_label},",
        "",
        f"Segue a relação de consultas agendadas para amanhã, {date_str}:",
        "",
    ]

    for i, appt in enumerate(appointments, 1):
        start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
        end_dt = datetime.fromisoformat(appt["end_time"]).astimezone(TZ)
        time_str = f"{start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}"

        user = appt.get("users") or {}
        patient_name = user.get("patient_name") or user.get("name") or "Paciente"
        modality = MODALITY_LABELS.get(appt.get("modality", ""), appt.get("modality", "—"))
        paid = "✅ Pago" if appt.get("paid_at") else "⏳ Aguardando pagamento"

        lines.append(f"{i}. {time_str} — {patient_name}")
        lines.append(f"   Modalidade: {modality} | {paid}")
        lines.append("")

    lines += [
        f"Total: {len(appointments)} consulta(s)",
        "",
        "— Eva, assistente virtual Psique",
    ]

    return subject, "\n".join(lines)


async def main():
    from supabase import acreate_client
    from app.email_sender import send_clinic_notification_email

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    now = datetime.now(TZ)
    tomorrow_start = (now.date() + timedelta(days=1)).isoformat()
    tomorrow_end = (now.date() + timedelta(days=2)).isoformat()
    date_str = (now.date() + timedelta(days=1)).strftime("%d/%m/%Y")

    # Fetch all tomorrow's scheduled appointments with patient and doctor info
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, end_time, doctor_id, modality, paid_at, users(patient_name, name)")
        .eq("status", "scheduled")
        .gte("start_time", f"{tomorrow_start}T00:00:00")
        .lt("start_time", f"{tomorrow_end}T00:00:00")
        .order("start_time")
        .execute()
    )

    appointments = result.data or []
    print(f"Tomorrow's appointments: {len(appointments)}")

    if not appointments:
        print("No appointments tomorrow — no emails sent.")
        return

    # Group by doctor
    by_doctor: dict[str, list] = {}
    for appt in appointments:
        doc_id = appt.get("doctor_id", "")
        by_doctor.setdefault(doc_id, []).append(appt)

    # Fetch doctor emails from doctors table
    doctors_result = await client.from_("doctors").select("doctor_id, agenda_id").execute()
    doctor_rows = {d["doctor_id"]: d for d in (doctors_result.data or [])}

    for doctor_id, appts in by_doctor.items():
        doctor_label = DOCTOR_LABELS.get(doctor_id, "Médico(a)")
        doctor_row = doctor_rows.get(doctor_id, {})

        doctor_email = doctor_row.get("agenda_id", "")
        if not doctor_email:
            print(f"  No email for {doctor_label} — skipping.")
            continue

        subject, body = _format_agenda_email(doctor_label, date_str, appts)

        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            smtp_host = os.environ.get("SMTP_HOST")
            smtp_port = int(os.environ.get("SMTP_PORT", "465"))
            smtp_user = os.environ.get("SMTP_USER")
            smtp_password = os.environ.get("SMTP_PASSWORD")

            if not all([smtp_host, smtp_user, smtp_password]):
                print("  SMTP not configured — skipping email send.")
                continue

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = doctor_email
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_user, doctor_email, msg.as_string())

            print(f"  Sent agenda to {doctor_email} — {doctor_label} ({len(appts)} appts)")
        except Exception as e:
            print(f"  Failed to send to {doctor_email}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
