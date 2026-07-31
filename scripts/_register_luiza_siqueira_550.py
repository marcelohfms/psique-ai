"""Registra o pagamento de R$ 550,00 da Luiza Siqueira Barbosa (558191183875).

Comprovante enviado em 19/06/2026 09:57, referente à consulta de 06/05/2026 às
14h (presencial, Dra. Bruna). A Eva chegou a chamar register_payment duas vezes,
mas a tool abortou com "Erro interno" — o KeyError('end_time') corrigido em
f87b656 — e o pagamento nunca entrou na planilha.

Reproduz o que register_payment teria feito: linha na planilha Pagamentos,
renomeio do arquivo no Drive no padrão da clínica e evento de auditoria.
NÃO cria linha em `appointments` (a consulta é anterior ao registro da paciente
no bot e nenhuma linha da tabela tem appointment_id nulo).
"""
import asyncio
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

PATIENT_NAME = "Luiza Siqueira Barbosa"
PATIENT_ID = "8a413411-f5ca-431a-aa46-5ede00a5b766"
PHONE = "558191183875"
DOCTOR_LABEL = "Dra. Bruna"
APPOINTMENT_DT = "06/05/2026 14:00"
AMOUNT = "550,00"
# Último dos dois envios do MESMO comprovante — é o que register_payment teria
# usado (ele varre o histórico de trás para frente).
DRIVE_LINK = "https://drive.google.com/file/d/1fItoHGJRgcFZXb3lBH27PpNwjI-JL27s/view?usp=drivesdk"


async def main():
    from app.database import get_supabase, log_event
    from app.google_sheets import append_payment_receipt
    from app.google_drive import rename_file

    client = await get_supabase()

    # Idempotência: se já houver evento de registro para esta paciente, aborta.
    evs = (await client.from_("events").select("metadata").eq("phone", PHONE).eq(
        "event_type", "payment_receipt_registered").execute()).data
    if evs:
        print("❌ Já existe payment_receipt_registered para este número — abortando.")
        return

    print(f"Paciente: {PATIENT_NAME}\nConsulta: {APPOINTMENT_DT} ({DOCTOR_LABEL}, presencial)")
    print(f"Valor: R$ {AMOUNT} — pago em 19/06/2026 09:57 via PIX")

    await append_payment_receipt(
        PATIENT_NAME, PHONE, DOCTOR_LABEL, APPOINTMENT_DT, AMOUNT, DRIVE_LINK,
        payment_type="Consulta", payment_method_override="PIX",
    )
    print("✅ Planilha Pagamentos: linha adicionada (Tipo=Consulta, PIX).")

    # Nome do arquivo no padrão da clínica: {Nome}_{DD-MM-AAAA}_R${valor}
    # (sem extensão — rename_file preserva a original).
    file_id = re.search(r"/d/([^/?&#\s]+)", DRIVE_LINK).group(1)
    new_name = f"{PATIENT_NAME.replace(' ', '_')}_06-05-2026_R$550-00"
    try:
        await rename_file(file_id, new_name)
        print(f"✅ Drive: arquivo renomeado para {new_name}")
    except Exception as e:
        print(f"⚠️  Drive: falha ao renomear ({e}) — o link continua correto.")

    await log_event("payment_receipt_registered", PHONE, {
        "patient_name": PATIENT_NAME,
        "patient_id": PATIENT_ID,
        "amount": AMOUNT,
        "payment_type": "Consulta",
        "payment_method": "PIX",
        "drive_link": DRIVE_LINK,
        "appointment_dt": APPOINTMENT_DT,
        "paid_at": "2026-06-19T09:57:49-03:00",
        "retroactive_registration": True,
        "reason": "register_payment abortou com KeyError('end_time') em 19/06/2026 (corrigido em f87b656); registrado manualmente",
    })
    print("✅ Evento payment_receipt_registered gravado.")

asyncio.run(main())
