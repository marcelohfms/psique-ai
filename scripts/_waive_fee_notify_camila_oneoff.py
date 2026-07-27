"""
One-off: isenta a taxa de reserva da consulta de hoje (27/07/2026 14:00, Dr.
Júlio, presencial) de Camila Marques Brasileiro pela proximidade do horário
(encaixe de última hora) e envia confirmação à paciente informando que ela
paga o valor integral da consulta de uma vez só (sem taxa de reserva separada).

Segue a mesma semântica da tool waive_booking_fee (app/graph/tools.py:3279):
booking_fee_waived=True + booking_fee_paid_at=now, para que o cancelamento
automático por falta de pagamento não trate a consulta como pendente.

Uso: uv run python scripts/_waive_fee_notify_camila_oneoff.py
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581987516312"
PATIENT_NAME = "Camila Marques Brasileiro"
APPOINTMENT_ID = "ld0krrs6c71q7dustdo3k3mt9c"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_AGE = 33


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _expected_consultation_amount
    from app.whatsapp import send_text

    client = await get_supabase()

    appt = await client.from_("appointments").select(
        "appointment_id, start_time, booking_fee_waived, booking_fee_paid_at, status"
    ).eq("appointment_id", APPOINTMENT_ID).maybe_single().execute()
    if not appt.data:
        print("⚠️  Agendamento não encontrado. Abortando.")
        return
    if appt.data.get("status") != "scheduled":
        print(f"⚠️  Status inesperado ({appt.data.get('status')}). Abortando.")
        return
    if appt.data.get("booking_fee_waived"):
        print("ℹ️  Taxa já estava isenta. Só reenviando confirmação.")
    else:
        now_iso = datetime.now(TZ).isoformat()
        await client.from_("appointments").update({
            "booking_fee_waived": True,
            "booking_fee_paid_at": now_iso,
        }).eq("appointment_id", APPOINTMENT_ID).execute()
        await log_event("booking_fee_waived", PHONE, {
            "appointment_id": APPOINTMENT_ID,
            "reason": "proximidade_do_horario_encaixe",
            "initiated_by": "clinic",
        })
        print("✅ Taxa de reserva isentada no banco.")

    now = datetime.now(TZ)
    valor_pix = _expected_consultation_amount("julio", PATIENT_AGE, None, now)
    valor_cartao = valor_pix + 50

    msg = (
        f"Olá, {PATIENT_NAME.split()[0]}! Confirmando seu agendamento: consulta com o "
        f"{DOCTOR_LABEL} hoje, 27/07, às 14h, presencial.\n\n"
        "Como o horário é muito próximo, vamos dispensar a taxa de reserva — você não "
        "precisa fazer nenhum pagamento antecipado.\n\n"
        f"Na hora, o pagamento é feito de uma vez só, o valor integral da consulta: "
        f"R$ {valor_cartao} no cartão ou R$ {valor_pix} no PIX/dinheiro.\n\n"
        "Qualquer dúvida, estou à disposição!"
    )
    await send_text(PHONE, msg)
    print("✅ Confirmação enviada à paciente.")
    print(msg)


if __name__ == "__main__":
    asyncio.run(main())
