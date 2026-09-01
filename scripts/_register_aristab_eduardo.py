"""Registro manual da solicitação de receita do ARISTAB — Eduardo Lyra Bezerra.

Contexto (27/08/2026 18h46): a mãe Camila Lyra (5581997828165) avisou que a receita
emitida não veio com o ARISTAB e precisa com urgência — Eduardo (16a, paciente do
Dr. Júlio) viaja amanhã com o pai e vai precisar do remédio. A Eva fez handoff fora
do horário e NÃO chamou request_document, então nada ficou registrado. Este script
reproduz o que o request_document faria (documents + evento + planilha) e envia um
e-mail próprio ao Dr. Júlio deixando claro o remédio e a urgência.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997828165"
PATIENT = "Eduardo Lyra Bezerra"
AGE = 16
PATIENT_EMAIL = "camilalyratpd@gmail.com"
DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
DOCTOR_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
DOCTOR_EMAIL = "dr.juliogouveia@gmail.com"
DOC_TYPE = "receita"
OBS = ("ARISTAB (aripiprazol) — não veio na receita emitida. URGENTE: Eduardo viaja "
       "amanhã (28/08) com o pai e precisa levar o remédio. Registrado manualmente "
       "após handoff fora do horário em 27/08.")


async def main():
    from app.database import get_supabase, log_event
    from app.google_sheets import append_document_request
    from app.email_sender import _send_email
    import os

    client = await get_supabase()

    # 1) documents (igual ao request_document)
    await client.from_("documents").insert({
        "content": f"Solicitação de {DOC_TYPE}",
        "metadata": {
            "type": DOC_TYPE,
            "patient_name": PATIENT,
            "patient_email": PATIENT_EMAIL,
            "doctor_id": DOCTOR_ID,
            "phone": PHONE,
            "medication_note": OBS,
            "manual_registration": True,
        },
    }).execute()
    print("[ok] documents inserido")

    # 2) evento
    await log_event("document_requested", PHONE, {
        "document_type": DOC_TYPE,
        "patient_name": PATIENT,
        "manual": True,
    })
    print("[ok] evento document_requested")

    # 3) planilha Solicitações
    await append_document_request(
        PATIENT, AGE, PHONE, PATIENT_EMAIL, DOC_TYPE,
        medication_note=OBS, doctor_name=DOCTOR_LABEL,
    )
    print("[ok] planilha Solicitações")

    # 4) e-mail próprio ao Dr. Júlio (com remédio + urgência)
    subject = f"URGENTE — Receita de ARISTAB para {PATIENT}"
    body = (
        f"{DOCTOR_LABEL},\n\n"
        f"A responsável pelo paciente {PATIENT} entrou em contato pelo WhatsApp "
        f"informando que a receita emitida NÃO veio com o ARISTAB (aripiprazol) e "
        f"precisa dele com urgência.\n\n"
        f"Motivo da urgência: o Eduardo viaja amanhã (28/08) com o pai e precisa levar "
        f"o remédio.\n\n"
        f"Dados do paciente:\n"
        f"  Nome: {PATIENT}\n"
        f"  Idade: {AGE} anos\n"
        f"  Telefone (mãe, Camila Lyra): {PHONE}\n"
        f"  Medicação: ARISTAB (aripiprazol)\n"
        f"  E-mail para envio da receita: {PATIENT_EMAIL}\n\n"
        f"Por favor, providencie a emissão da receita do ARISTAB e envie no e-mail acima "
        f"assim que possível.\n\n"
        f"— Registro manual da equipe (pedido chegou fora do horário de atendimento)"
    )
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _send_email,
        os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "465")),
        os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"],
        DOCTOR_EMAIL, subject, body,
    )
    print("[ok] e-mail enviado ao Dr. Júlio")

    # 5) evento de notificação (rastreio anti-duplicidade)
    await log_event("document_doctor_notified", PHONE, {
        "document_type": DOC_TYPE,
        "patient_name": PATIENT,
        "doctor_id": DOCTOR_ID,
        "manual": True,
    })
    print("[ok] evento document_doctor_notified")


asyncio.run(main())
