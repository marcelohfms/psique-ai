import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

APPT_ID = "0715f05f-d493-4eef-be51-c8b13c2cc6e4"
PATIENT_NAME = "Matheus Silva Mônica Lopes"
PATIENT_PHONE = "5581988851971@s.whatsapp.net"
DOCTOR_LABEL = "Dra. Bruna"
APPOINTMENT_DT = "15/07/2026 17:00"
AMOUNT = "100,00"
CHATWOOT_IMAGE_URL = (
    "https://evolution-chatwoot.5pqooc.easypanel.host/rails/active_storage/blobs/redirect/"
    "eyJfcmFpbHMiOnsibWVzc2FnZSI6IkJBaHBBcmNDIiwiZXhwIjpudWxsLCJwdXIiOiJibG9iX2lkIn19"
    "--f2f6d95b99717235b23df68ef62782f6e47051dd/File.jpg"
)


async def main():
    import httpx
    from app.database import get_supabase, log_event
    from app.google_drive import upload_image, rename_file
    from app.google_sheets import append_payment_receipt
    from app.email_sender import send_clinic_notification_email

    client = await get_supabase()

    # Safety: re-check the appointment hasn't been paid in the meantime.
    appt = (await client.from_("appointments").select("*").eq("id", APPT_ID).execute()).data
    if not appt:
        print("Agendamento não encontrado — abortando.")
        return
    appt = appt[0]
    if appt.get("booking_fee_paid_at"):
        print(f"Já está registrado (booking_fee_paid_at={appt['booking_fee_paid_at']}) — abortando para evitar duplicidade.")
        return

    # 1. Download the receipt image from Chatwoot.
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as hc:
        img_resp = await hc.get(CHATWOOT_IMAGE_URL)
        img_resp.raise_for_status()
        image_bytes = img_resp.content
    print(f"Imagem baixada: {len(image_bytes)} bytes")

    # 2. Upload to the payments Drive folder (same naming pattern as app/media.py).
    now_str = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    safe_patient = PATIENT_NAME.replace(" ", "_")
    upload_filename = f"comprovante_{safe_patient}_{now_str}.jpg"
    drive_link = await upload_image(image_bytes, upload_filename, "image/jpeg")
    print(f"Drive upload OK: {drive_link}")

    # 3. Rename to the register_payment convention: {Nome}_{DD-MM-AAAA}_R${valor}
    fid_match = re.search(r'/d/([^/?&#\s]+)', drive_link) or re.search(r'[?&]id=([^?&#\s]+)', drive_link)
    if fid_match:
        file_id = fid_match.group(1)
        date_clean = APPOINTMENT_DT.split(" ")[0].replace("/", "-")
        amount_clean = AMOUNT.replace(",", "-").replace(".", "-")
        new_filename = f"{safe_patient}_{date_clean}_R${amount_clean}"
        await rename_file(file_id, new_filename)
        print(f"Drive rename OK: {new_filename}")
    else:
        print("AVISO: não consegui extrair file_id do drive_link — rename pulado.")

    # 4. Update the appointment — booking fee paid (taxa de reserva, not full payment).
    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "booking_fee_paid_at": now_iso,
        "updated_at": now_iso,
    }).eq("appointment_id", appt["appointment_id"]).execute()
    print("Appointment atualizado: booking_fee_paid_at =", now_iso)

    # 5. Record in the Pagamentos spreadsheet.
    await append_payment_receipt(
        PATIENT_NAME, PATIENT_PHONE, DOCTOR_LABEL, APPOINTMENT_DT, AMOUNT, drive_link,
        payment_type="Taxa de Reserva", payment_method_override="PIX",
    )
    print("Planilha de pagamentos atualizada.")

    # 6. Notify the clinic by email, confirming receipt (manual registration, Eva was inactive).
    subject = f"Comprovante recebido — {PATIENT_NAME}"
    body = (
        f"💰 Comprovante recebido e registrado manualmente!\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Valor: R$ {AMOUNT}\n"
        f"Tipo: Taxa de Reserva\n"
        f"Consulta: {APPOINTMENT_DT} com {DOCTOR_LABEL}\n"
        f"Link: {drive_link}\n\n"
        f"Observação: este comprovante foi enviado pela paciente/responsável às 17:34 (horário de "
        f"Brasília) de 07/07/2026 diretamente no Chatwoot, enquanto a Eva estava marcada como inativa "
        f"(conversa transferida para atendimento humano). Por isso não passou pelo pipeline automático "
        f"de reconhecimento de comprovante — foi identificado e registrado manualmente nesta revisão."
    )
    await send_clinic_notification_email(subject, body)
    print("E-mail de confirmação enviado à clínica.")

    # 7. Log the event.
    await log_event("payment_receipt_registered", PATIENT_PHONE, {
        "patient_name": PATIENT_NAME,
        "amount": AMOUNT,
        "payment_type": "Taxa de Reserva",
        "drive_link": drive_link,
        "registered_manually": True,
    })
    print("Evento registrado em `events`.")

    print("\n✅ Fluxo completo.")

asyncio.run(main())
