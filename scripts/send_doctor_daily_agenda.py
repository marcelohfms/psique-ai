"""
Send each doctor their appointment list for the next day via email.
Runs every day at 18h Recife time via GitHub Actions.

Queries Google Calendar (source of truth) for tomorrow's events per doctor,
enriches with payment status from the appointments DB table, and sends
one email per doctor with the full list.
"""
import asyncio
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}
DOCTOR_LABELS = {
    "julio": "Dr. Júlio",
    "bruna": "Dra. Bruna",
}
MODALITY_LABELS = {
    "online": "Online",
    "presencial": "Presencial",
    "escolha": "Online/Presencial",
}


def _credentials():
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )


def _list_calendar_events(calendar_id: str, time_min: datetime, time_max: datetime) -> list[dict]:
    """Fetch non-cancelled, non-all-day events from a Google Calendar in the given window."""
    from googleapiclient.discovery import build
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min.isoformat(),
        timeMax=time_max.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = []
    for evt in result.get("items", []):
        if evt.get("status") == "cancelled":
            continue
        start_raw = evt.get("start", {})
        if "dateTime" not in start_raw:
            continue  # skip all-day events
        events.append(evt)
    return events


def _format_agenda_email(
    doctor_label: str,
    date_str: str,
    appointments: list[dict],
) -> tuple[str, str]:
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

        patient_name = appt.get("patient_name") or "Paciente"
        modality_raw = appt.get("modality", "")
        modality = MODALITY_LABELS.get(modality_raw, modality_raw) if modality_raw else "—"
        paid = "✅ Pago" if appt.get("paid_at") else "⏳ Aguardando pagamento"

        lines.append(f"{i}. {time_str} — {patient_name}")
        if modality_raw:
            lines.append(f"   Modalidade: {modality} | {paid}")
        else:
            lines.append(f"   {paid}")
        lines.append("")

    lines += [
        f"Total: {len(appointments)} consulta(s)",
        "",
        "— Eva, assistente virtual Psique",
    ]

    return subject, "\n".join(lines)


def _send_email(to_email: str, subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")

    if not all([smtp_host, smtp_user, smtp_password]):
        print("  SMTP not configured — skipping email send.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_email, msg.as_string())


async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    now = datetime.now(TZ)
    tomorrow = now.date() + timedelta(days=1)
    # Use Recife-timezone-aware boundaries (offset -03:00)
    time_min = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, tzinfo=TZ)
    time_max = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59, tzinfo=TZ)
    date_str = tomorrow.strftime("%d/%m/%Y")

    # Fetch doctor records (agenda_id = Google Calendar ID; also used as email)
    doctors_result = await client.from_("doctors").select("doctor_id, agenda_id").execute()
    doctor_rows = {d["doctor_id"]: d for d in (doctors_result.data or [])}

    # Prefetch DB appointments for tomorrow (to enrich with payment status and patient name)
    # Use timezone-aware ISO strings so PostgREST interprets them correctly
    db_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, end_time, doctor_id, modality, paid_at, users(patient_name, name)")
        .eq("status", "scheduled")
        .gte("start_time", time_min.isoformat())
        .lt("start_time", time_max.isoformat())
        .execute()
    )
    db_appts: dict[str, dict] = {}
    for row in (db_result.data or []):
        db_appts[row["appointment_id"]] = row

    print(f"DB appointments for {date_str}: {len(db_appts)}")

    any_sent = False
    for doctor_id, doctor_key in DOCTOR_KEYS.items():
        doctor_label = DOCTOR_LABELS[doctor_key]
        doctor_row = doctor_rows.get(doctor_id, {})
        calendar_id = doctor_row.get("agenda_id", "")
        if not calendar_id:
            print(f"  No calendar/email for {doctor_label} — skipping.")
            continue

        # Query Google Calendar for tomorrow's events (source of truth)
        try:
            loop = asyncio.get_event_loop()
            cal_events = await loop.run_in_executor(
                None, _list_calendar_events, calendar_id, time_min, time_max
            )
        except Exception as e:
            print(f"  Calendar fetch failed for {doctor_label}: {e}")
            cal_events = []

        if not cal_events:
            print(f"  No calendar events for {doctor_label} tomorrow — skipping email.")
            continue

        print(f"  {doctor_label}: {len(cal_events)} calendar event(s)")

        # Build enriched appointment list
        appointments = []
        for evt in cal_events:
            event_id = evt.get("id", "")
            start_raw = evt["start"]["dateTime"]
            end_raw = evt["end"]["dateTime"]

            # Try to match with DB record for payment status and patient name
            db_row = db_appts.get(event_id)
            if db_row:
                user = db_row.get("users") or {}
                patient_name = user.get("patient_name") or user.get("name") or ""
                modality = db_row.get("modality", "")
                paid_at = db_row.get("paid_at")
            else:
                # Manually-booked event — extract patient name from event summary
                summary = evt.get("summary", "")
                # Strip prefixes like "Consulta — " or "Consulta - "
                patient_name = summary.replace("Consulta —", "").replace("Consulta -", "").strip()
                # Strip modality suffix like " [Online]" or " [Presencial]"
                for suffix in (" [Online]", " [Presencial]", " [online]", " [presencial]"):
                    patient_name = patient_name.replace(suffix, "")
                modality = ""
                paid_at = None

            appointments.append({
                "start_time": start_raw,
                "end_time": end_raw,
                "patient_name": patient_name or evt.get("summary", "Paciente"),
                "modality": modality,
                "paid_at": paid_at,
            })

        # Sort by start time
        appointments.sort(key=lambda a: a["start_time"])

        subject, body = _format_agenda_email(doctor_label, date_str, appointments)

        try:
            _send_email(calendar_id, subject, body)
            print(f"  Sent agenda to {calendar_id} — {doctor_label} ({len(appointments)} appts)")
            any_sent = True
        except Exception as e:
            print(f"  Failed to send to {calendar_id}: {e}")

    if not any_sent:
        print("No emails sent.")


if __name__ == "__main__":
    asyncio.run(main())
