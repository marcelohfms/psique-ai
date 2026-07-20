import asyncio
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")

_DOCTOR_LABELS = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}
_DOCUMENT_LABELS = {
    "nota_fiscal": "Nota Fiscal",
    "laudo": "Laudo",
    "exame": "Exame",
    "relatorio": "Relatório",
    "receita": "Receita",
    "declaracao": "Declaração",
    "requisicao": "Requisição",
}


def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_email, msg.as_string())


async def send_clinic_notification_email(subject: str, body: str) -> None:
    """Send a notification email to the clinic address (CLINIC_NOTIFY_EMAIL).
    Does nothing if SMTP credentials or CLINIC_NOTIFY_EMAIL are not configured.
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("CLINIC_NOTIFY_EMAIL")

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _send_email,
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        to_email,
        subject,
        body,
    )



async def send_document_nudge_email(
    doctor_key: str,
    doctor_email: str,
    patient_name: str,
    patient_age: int | None,
    phone: str,
    patient_email: str,
    document_type: str,
    patient_message: str,
    requested_at: str,
) -> None:
    """Send an urgency nudge email to the doctor when a patient follows up on a pending document."""
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = doctor_email

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    doctor_label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
    doc_label = _DOCUMENT_LABELS.get(document_type, document_type)
    phone_clean = phone.replace("@s.whatsapp.net", "")
    age_str = f"{patient_age} anos" if patient_age else "não informada"
    now = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")

    subject = f"⚠️ Cobrança de paciente — {doc_label} pendente — {patient_name}"
    body = (
        f"{doctor_label},\n\n"
        f"O(a) paciente {patient_name} está aguardando a emissão de {doc_label} "
        f"solicitado(a) em {requested_at} e enviou a seguinte mensagem agora ({now}):\n\n"
        f"  \"{patient_message}\"\n\n"
        f"Dados do paciente:\n"
        f"  Nome: {patient_name}\n"
        f"  Idade: {age_str}\n"
        f"  Telefone: {phone_clean}\n"
        f"  E-mail para envio: {patient_email}\n"
        f"  Tipo de documento: {doc_label}\n\n"
        f"Por favor, providencie a emissão e envie ao paciente no e-mail acima.\n\n"
        f"— Eva, assistente virtual Psique"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _send_email,
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        to_email,
        subject,
        body,
    )


async def send_document_request_email(
    doctor_key: str,
    doctor_email: str,
    patient_name: str,
    patient_age: int | None,
    phone: str,
    patient_email: str,
    document_type: str,
    financial_name: str = "",
    financial_cpf: str = "",
    financial_email: str = "",
) -> None:
    """Send an email to the responsible doctor notifying a document request.
    Does nothing if SMTP credentials or doctor email are not configured.
    doctor_email: fetched from doctors.agenda_id in Supabase.
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = doctor_email

    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    doctor_label = _DOCTOR_LABELS.get(doctor_key, doctor_key)
    doc_label = _DOCUMENT_LABELS.get(document_type, document_type)
    phone_clean = phone.replace("@s.whatsapp.net", "")
    age_str = f"{patient_age} anos" if patient_age else "não informada"
    now = datetime.now(TZ).strftime("%d/%m/%Y às %H:%M")

    # Para nota fiscal, usa dados do responsável financeiro no lugar dos dados do paciente
    if document_type == "nota_fiscal":
        billing_name = financial_name or patient_name
        billing_email = financial_email or patient_email
        billing_cpf = financial_cpf or ""
    else:
        billing_name = patient_name
        billing_email = patient_email
        billing_cpf = ""

    subject = f"Solicitação de {doc_label} — {patient_name}"
    cpf_line = f"  CPF: {billing_cpf}\n" if billing_cpf else ""
    referencia_line = (
        f"  Observação: Referente à consulta do paciente {patient_name}\n"
        if document_type == "nota_fiscal" and financial_name and financial_name != patient_name
        else ""
    )
    body = (
        f"{doctor_label},\n\n"
        f"Um paciente solicitou a emissão de {doc_label} via WhatsApp.\n\n"
        f"Dados do paciente:\n"
        f"  Nome: {patient_name}\n"
        f"  Idade: {age_str}\n"
        f"  Telefone: {phone_clean}\n"
        f"  Tipo de documento: {doc_label}\n"
        f"  Data da solicitação: {now}\n\n"
        f"Dados para emissão:\n"
        f"  Nome: {billing_name}\n"
        f"{cpf_line}"
        f"  E-mail para envio: {billing_email}\n"
        f"{referencia_line}\n"
        f"Por favor, providencie a emissão e envie no e-mail acima.\n\n"
        f"— Eva, assistente virtual Psique"
    )

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        _send_email,
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_password,
        to_email,
        subject,
        body,
    )
