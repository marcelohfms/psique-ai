"""
One-off: corrige a 1ª consulta (dividida) do menor Bernardo Rabelo Porto Ferreira
(tel 5581991320003, patient_id c73a61f1-...), Dr. Júlio.

O agendamento foi feito por engano como bloco único de 2h (23/07 19:00-21:00),
mas o Dr. Júlio não tinha 2h seguidas disponíveis. A consulta são DOIS MOMENTOS
de 1h da MESMA primeira consulta:
  - Momento 1 (responsáveis): 23/07/2026 19:00-20:00  → encolhe o evento existente
  - Momento 2 (paciente):     30/07/2026 15:00-16:00  → cria novo evento + linha

A taxa de reserva já foi paga (booking_fee_paid_at no slot 1); espelho no slot 2
para manter o invariante de que ambos os slots da consulta dividida compartilham
o estado da taxa (ver register_payment / painel de pagamento).
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

PATIENT_ID = "c73a61f1-52ef-42fa-b468-44ddb546d878"
CONTACT_ID = "cd596da3-82c4-4202-a7f3-19dcf86a93a6"
DOCTOR_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"  # julio
ROW1_ID = "c552249a-fe3f-44da-8770-a2ea8ba15b05"
EVENT1_ID = "lcko8pe0h2b0suggnfed54lclk"
BOOKING_FEE_PAID_AT = "2026-07-09T14:05:21.698073+00:00"

PATIENT_NAME = "Bernardo Rabelo Porto Ferreira"
PATIENT_EMAIL = "frederico-ferreira@hotmail.com"
PATIENT_NUMBER = "5581991320003"
DOCTOR_LABEL = "Dr. Júlio"
MODALITY = "presencial"

M1_START = datetime(2026, 7, 23, 19, 0, tzinfo=TZ)
M1_END = datetime(2026, 7, 23, 20, 0, tzinfo=TZ)
M2_START = datetime(2026, 7, 30, 15, 0, tzinfo=TZ)

NOTE1 = "1ª hora — responsáveis"
NOTE2 = "2ª hora — paciente"


def _description(note):
    return (
        f"Paciente: {PATIENT_NAME}\n"
        f"Médico: {DOCTOR_LABEL}\n"
        f"Modalidade: Presencial\n"
        f"Número: {PATIENT_NUMBER}\n"
        f"E-mail: {PATIENT_EMAIL}\n\n"
        f"{note}"
    )


def _summary(note):
    return f"Consulta — {PATIENT_NAME} [Presencial] ({note})"


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import create_event, _credentials, TIMEZONE
    from googleapiclient.discovery import build

    client = await get_supabase()
    calendar_id = await _get_doctor_calendar_id("julio")
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    # ── Momento 1: encolhe evento existente 19:00-21:00 → 19:00-20:00 ──────────
    patch = {
        "summary": _summary(NOTE1),
        "description": _description(NOTE1),
        "start": {"dateTime": M1_START.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": M1_END.isoformat(), "timeZone": TIMEZONE},
    }
    service.events().patch(calendarId=calendar_id, eventId=EVENT1_ID, body=patch).execute()
    print(f"[M1] Evento {EVENT1_ID} ajustado para 23/07 19:00-20:00 ({NOTE1}).")

    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "end_time": M1_END.isoformat(),
        "updated_at": now_iso,
    }).eq("id", ROW1_ID).execute()
    print(f"[M1] Linha {ROW1_ID} end_time -> {M1_END.isoformat()}.")

    # ── Momento 2: cria novo evento 30/07 15:00-16:00 + linha ─────────────────
    event2_id = await create_event(
        calendar_id=calendar_id,
        start=M2_START,
        slot_minutes=60,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        session_note=NOTE2,
        modality=MODALITY,
        patient_email=PATIENT_EMAIL,
        patient_number=PATIENT_NUMBER,
    )
    print(f"[M2] Evento criado {event2_id} para 30/07 15:00-16:00 ({NOTE2}).")

    await client.from_("appointments").insert({
        "patient_id": PATIENT_ID,
        "contact_id": CONTACT_ID,
        "doctor_id": DOCTOR_ID,
        "appointment_id": event2_id,
        "start_time": M2_START.isoformat(),
        "end_time": (M2_START + timedelta(minutes=60)).isoformat(),
        "status": "scheduled",
        "modality": MODALITY,
        "consultation_type": "primeira_consulta",
        "booking_fee_waived": False,
        "booking_fee_paid_at": BOOKING_FEE_PAID_AT,
    }).execute()
    print(f"[M2] Linha inserida (primeira_consulta, booking_fee_paid_at espelhado).")

    # ── Confirmação ───────────────────────────────────────────────────────────
    appts = await client.from_("appointments").select(
        "id, appointment_id, start_time, end_time, status, consultation_type, modality, booking_fee_paid_at"
    ).eq("patient_id", PATIENT_ID).order("start_time").execute()
    print("\n=== Estado final ===")
    for a in appts.data:
        st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m %H:%M")
        et = datetime.fromisoformat(a["end_time"]).astimezone(TZ).strftime("%H:%M")
        print(f"  {st}-{et} | {a['status']} | {a['consultation_type']} | {a['modality']} | fee_paid={bool(a['booking_fee_paid_at'])} | evt={a['appointment_id']}")


asyncio.run(main())
