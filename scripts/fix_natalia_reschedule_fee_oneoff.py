"""
One-off: corrige o registro da Natalia Camara Lima Alves De Souza Pimentel
(5581996332827) após bug de remarcação <24h.

Histórico:
- 01/07/2026 10:00 (Dra. Bruna) — consulta original, taxa de R$100 paga em 26/06.
- 01/07 09:46 — Natalia pediu remarcação faltando ~14min para a consulta (<24h).
  A Eva respondeu incorretamente que era "reagendamento grátis" (bug corrigido
  no commit 46beda6, publicado 3h30 depois, às 13:16).
- Pela política (remarcação <24h com taxa já paga): a taxa antiga deveria ser
  recolhida e uma NOVA taxa cobrada para a nova consulta.
- A consulta foi remarcada para 06/07/2026 07:30 (mesma linha no banco,
  reaproveitando appointment_id/booking_fee_paid_at do agendamento antigo).

Correção: trata o agendamento de 06/07 como uma consulta NOVA — reseta
created_at e zera booking_fee_paid_at — e cobra a nova taxa de reserva da
paciente.
"""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996332827@s.whatsapp.net"
APPOINTMENT_ID = "bb3psfo966q5vqhq0kc4bifpms"
PATIENT_NAME = "Natalia"
DOCTOR_LABEL = "Dra. Bruna"
APPT_DT_LABEL = "segunda-feira, 06/07/2026 às 07:30"
PIX_KEY = "42006848000178"


async def main():
    from app.database import get_supabase, log_event

    client = await get_supabase()
    now = datetime.now(TZ)

    # 1. Trata a consulta de 06/07 como nova: reseta created_at e zera a taxa antiga.
    await client.from_("appointments").update({
        "created_at": now.isoformat(),
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Agendamento atualizado: created_at resetado e booking_fee_paid_at zerado.")

    # 2. Cobra a nova taxa de reserva via WhatsApp
    msg = (
        f"{PATIENT_NAME}, tudo bem? 😊\n\n"
        "Revisando seu agendamento, identificamos que a remarcação da sua consulta "
        "foi solicitada faltando poucos minutos para o horário original (01/07 às 10:00), "
        "ou seja, dentro do prazo de 24h. Pela nossa política, nesses casos a taxa de "
        "reserva paga anteriormente não pode ser aproveitada para a nova data — pedimos "
        "desculpas por não termos avisado isso corretamente na hora.\n\n"
        f"Sua consulta com a {DOCTOR_LABEL} está marcada para *{APPT_DT_LABEL}* (online), "
        "mas para garantir a vaga é necessário o pagamento de uma nova taxa de reserva de "
        "R$ 100,00.\n"
        f"💳 PIX: {PIX_KEY}\n\n"
        "Esse valor será abatido do total da consulta. Assim que enviar o comprovante, "
        "confirmamos por aqui!"
    )
    from app.whatsapp import send_text
    try:
        await send_text(PHONE, msg)
        print("✅ Mensagem de cobrança enviada à Natalia.")
    except Exception as e:
        print(f"⚠️  WhatsApp: {e}")

    # 3. Notificar clínica
    from app.email_sender import send_clinic_notification_email
    try:
        await send_clinic_notification_email(
            f"Correção: nova taxa de reserva cobrada — {PATIENT_NAME}",
            f"Paciente: {PATIENT_NAME}\nTelefone: {PHONE.replace('@s.whatsapp.net', '')}\n"
            f"Médico: {DOCTOR_LABEL}\nConsulta: {APPT_DT_LABEL}\n\n"
            "Correção manual: a remarcação de 01/07 (bug de <24h, corrigido no commit "
            "46beda6) havia aproveitado indevidamente a taxa antiga para a nova consulta. "
            "booking_fee_paid_at foi zerado e uma nova cobrança de R$ 100,00 foi solicitada "
            "à paciente via WhatsApp. Aguardando comprovante.",
        )
        print("✅ Clínica notificada por e-mail.")
    except Exception as e:
        print(f"⚠️  E-mail: {e}")

    await log_event("booking_fee_charge_requested", PHONE, {
        "appointment_id": APPOINTMENT_ID,
        "reason": "correção manual — taxa antiga indevidamente aproveitada após remarcação <24h",
        "amount": "100,00",
    })
    print("✅ Evento registrado.")


asyncio.run(main())
