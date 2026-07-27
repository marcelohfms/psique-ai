"""One-off fix — Maria Beatriz Cavalcante Zamorano (contato Marcia, 5581996566872).

Incidente 2026-07-24: a consulta de 06/08 (19h, Dr. Júlio, acompanhamento) foi
INSERIDA MANUALMENTE no banco (linha appointments 769c033a-...), fora do fluxo da
Eva. O insert manual:
  - preencheu `booking_fee_paid_at = 2026-07-24T13:52:05` mesmo sem a taxa de
    reserva ter sido paga → a régua de cobrança
    (scripts/send_pending_payments_reminder.py) só pega
    `booking_fee_paid_at IS NULL`, então a consulta NUNCA entrou na fila de taxa
    pendente e a taxa jamais foi cobrada;
  - não criou evento no Google Calendar (appointment_id era um UUID, não um ID de
    evento gcal) → o horário de 06/08 19h não estava bloqueado na agenda do
    Dr. Júlio;
  - não gravou `modality` nem `contact_id`.

Correção (escopo "taxa + integridade", aprovado pelo usuário):
  1. Cria o evento no Google Calendar do Dr. Júlio (06/08 19:00–20:00, presencial)
     e vincula o appointment_id ao ID do evento gcal.
  2. Zera `booking_fee_paid_at` → a taxa de reserva volta a ser cobrável.
  3. Preenche `modality='presencial'` e `contact_id`.

Verificado antes de rodar: 06/08/2026 = quinta-feira (19h dentro do horário do
Dr. Júlio, encerra 20h — guard de fim de dia OK); 0 eventos na agenda do Dr. Júlio
em 06/08 (sem entrada manual/CAIXA ALTA para sombrear); payments vazio para o
paciente (taxa realmente não paga).
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")

ROW_ID = "f95fc053-3be0-4b86-8450-d18276235cb0"          # appointments.id (PK estável)
OLD_APPT_ID = "769c033a-4a01-4580-9361-6b6b62807809"     # UUID default (sem evento gcal)
PATIENT_NAME = "Maria Beatriz Cavalcante Zamorano"
PATIENT_EMAIL = "mczamor@hotmail.com"
PHONE = "5581996566872"
CONTACT_ID = "99e8dda4-9d50-4768-831a-322dc52c50f9"


async def main():
    from app.database import get_supabase
    from app.google_calendar import create_event
    from app.graph.tools import _get_doctor_calendar_id

    client = await get_supabase()

    # --- Guard: confirma o estado atual antes de alterar ---
    cur = (
        await client.from_("appointments")
        .select("id, appointment_id, start_time, status, booking_fee_paid_at, modality")
        .eq("id", ROW_ID)
        .single()
        .execute()
    )
    appt = cur.data
    print("Estado atual:", appt)
    assert appt["appointment_id"] == OLD_APPT_ID, "appointment_id inesperado — abortando"
    assert appt["status"] == "scheduled", f"status inesperado ({appt['status']}) — abortando"

    start = datetime(2026, 8, 6, 19, 0, 0, tzinfo=TZ)

    # --- 1. Cria o evento no Google Calendar do Dr. Júlio ---
    calendar_id = await _get_doctor_calendar_id("julio")
    event_id = await create_event(
        calendar_id=calendar_id,
        start=start,
        slot_minutes=60,
        patient_name=PATIENT_NAME,
        doctor_name="Júlio",
        modality="presencial",
        patient_email=PATIENT_EMAIL,
        patient_number=PHONE,
    )
    print(f"[1] Evento criado no Google Calendar: {event_id}")

    # --- 2+3. Vincula gcal, zera taxa, preenche modality/contact ---
    upd = (
        await client.from_("appointments")
        .update(
            {
                "appointment_id": event_id,
                "booking_fee_paid_at": None,
                "modality": "presencial",
                "contact_id": CONTACT_ID,
                "updated_at": datetime.now(TZ).isoformat(),
            }
        )
        .eq("id", ROW_ID)
        .execute()
    )
    print(f"[2/3] Linha atualizada:", upd.data)
    print(
        "\n✅ Pronto. A taxa de reserva agora aparece como pendente "
        "(booking_fee_paid_at = NULL) e o slot 06/08 19h está bloqueado na agenda."
    )


asyncio.run(main())
