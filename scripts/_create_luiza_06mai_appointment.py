"""Cria a linha de `appointments` da consulta de 06/05/2026 14h da Luiza Siqueira
Barbosa (presencial, Dra. Bruna), que aconteceu fora do bot.

O `appointment_id` NÃO é um evento do Google Calendar: recebe o prefixo
"manual-" justamente para deixar explícito que a linha foi lançada à mão e que
não existe evento correspondente na agenda. Todo id criado pelo bot é uma string
base32 de 26 caracteres vinda da API do Calendar, então o prefixo nunca colide —
e qualquer tentativa de cancelar/atualizar esse "evento" simplesmente não
encontra nada (o erro já é tratado como não-fatal em cancel_calendar_event).

Segurança verificada antes de inserir:
  - status=completed + paid_at preenchido → fora de complete_appointments,
    send_payment_reminders e da seção "Consultas" que a Eva lê
    (get_upcoming_appointments só traz completed COM saldo em aberto)
  - pos_consulta_sent_at preenchido → nenhuma mensagem retroativa de pós-consulta
  - a paciente não tem linha em return_reminders
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

PATIENT_ID = "8a413411-f5ca-431a-aa46-5ede00a5b766"
CONTACT_ID = "2e5c0fec-01c9-4609-b466-67de74e252c3"
DOCTOR_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"  # Dra. Bruna
PHONE = "558191183875"
APPOINTMENT_ID = "manual-2026-05-06-luiza-siqueira-barbosa"
PAID_AT = "2026-06-19T09:57:49-03:00"  # horário da transação no comprovante


async def main():
    from app.database import get_supabase, log_event
    client = await get_supabase()

    existing = (await client.from_("appointments").select("id, appointment_id").eq(
        "patient_id", PATIENT_ID).execute()).data
    if existing:
        print(f"❌ A paciente já tem {len(existing)} consulta(s) no banco — abortando.")
        for e in existing:
            print("   ", e)
        return

    start = datetime(2026, 5, 6, 14, 0, tzinfo=TZ)
    end = datetime(2026, 5, 6, 15, 0, tzinfo=TZ)
    now_iso = datetime.now(TZ).isoformat()

    row = {
        "appointment_id": APPOINTMENT_ID,
        "patient_id": PATIENT_ID,
        "contact_id": CONTACT_ID,
        "doctor_id": DOCTOR_ID,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "status": "completed",
        "modality": "presencial",
        "consultation_type": "acompanhamento",
        "booking_fee_paid_at": PAID_AT,
        "paid_at": PAID_AT,
        "booking_fee_waived": False,
        "pos_consulta_sent_at": now_iso,   # consulta de maio: nada de mensagem retroativa
        "updated_at": now_iso,
    }
    result = await client.from_("appointments").insert(row).execute()
    print("✅ Linha criada:")
    for k, v in (result.data[0] if result.data else row).items():
        print(f"   {k}: {v}")

    await log_event("appointment_backfilled_manually", PHONE, {
        "appointment_id": APPOINTMENT_ID,
        "patient_id": PATIENT_ID,
        "start_time": start.isoformat(),
        "created_by": "script _create_luiza_06mai_appointment.py",
        "not_created_by_bot": True,
        "no_calendar_event": True,
        "reason": "Consulta ocorrida fora do bot (06/05/2026 14h, presencial, Dra. Bruna). "
                  "Lançada à mão para o pagamento de R$ 550,00 de 19/06 ter a que se referir.",
    })
    print("✅ Evento appointment_backfilled_manually gravado.")

asyncio.run(main())
