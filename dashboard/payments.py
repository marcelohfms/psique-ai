"""Lógica de pagamentos pendentes, compartilhada entre a página cheia
(/pagamentos, Basic Auth) e o painel da atendente embutido no Chatwoot
(token, filtrado por paciente).
"""
import asyncio
import logging
import os
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("America/Recife")
_PAYMENTS_SHEET_RANGE = "Pagamentos!A:J"

DOCTOR_DISPLAY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}

DOCTOR_KEY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}

FORMA_PAGAMENTO_LABEL = {
    "PIX": "PIX",
    "cartao_credito": "Cartão de crédito",
    "cartao_debito": "Cartão de débito",
    "dinheiro": "Dinheiro",
}


def _calc_valor_consulta(
    doctor_id: str,
    birth_date: str | None,
    consultation_type: str | None,
    custom_price: int | None,
) -> int:
    """Retorna o valor sugerido da consulta (com desconto de R$50 para dinheiro/PIX)."""
    if custom_price is not None:
        return custom_price
    age = None
    if birth_date:
        try:
            bd = date.fromisoformat(birth_date)
            today = date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except ValueError:
            pass

    doctor_key = DOCTOR_KEY.get(doctor_id, "")
    post_june = (date.today().year, date.today().month) >= (2026, 6)

    if doctor_key == "bruna":
        base = 700 if post_june else 600
    elif doctor_key == "julio":
        if age is None or age >= 18:
            base = 700 if post_june else 600
        elif consultation_type == "primeira_consulta":
            base = 850 if post_june else 750
        else:
            base = 750 if post_june else 650
    else:
        base = 700 if post_june else 600

    return base - 50  # desconto PIX/dinheiro


async def _send_clinic_email(subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    to_email = os.environ.get("CLINIC_NOTIFY_EMAIL")
    if not all([smtp_host, smtp_user, smtp_password, to_email]):
        return

    def _send() -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send)


async def _append_payment_sheet(
    patient_name: str,
    phone: str,
    doctor_name: str,
    appointment_dt: str,
    amount: str,
    payment_type: str,
    payment_method: str,
) -> None:
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    if not spreadsheet_id:
        return

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    now = datetime.now(_TZ).strftime("%d/%m/%Y %H:%M")
    row = [now, patient_name, doctor_name, appointment_dt, amount, phone, payment_type, payment_method, "", ""]

    def _write() -> None:
        service = build("sheets", "v4", credentials=creds)
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=_PAYMENTS_SHEET_RANGE,
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write)


async def compute_pendencias(client, patient_ids: list[str] | None = None) -> list[dict]:
    """Retorna a lista de pendências (taxa/consulta) em aberto.

    Sem `patient_ids`: todas as pendências da clínica (usado por /pagamentos).
    Com `patient_ids`: só as pendências desses pacientes (usado pelo painel da atendente).
    Lista vazia em `patient_ids` retorna `[]` sem consultar o banco.
    """
    if patient_ids is not None and not patient_ids:
        return []

    query = (
        client.from_("appointments")
        .select(
            "appointment_id, start_time, doctor_id, paid_at, "
            "booking_fee_paid_at, booking_fee_waived, consultation_type, status, "
            "patients(name, birth_date, custom_price, "
            "patient_contacts(is_self, contacts(phone, name)))"
        )
        .in_("status", ["scheduled", "completed"])
    )
    if patient_ids is not None:
        query = query.in_("patient_id", patient_ids)
    result = await query.execute()

    pendencias = []
    for appt in result.data or []:
        patient = appt.get("patients") or {}
        patient_name = patient.get("name") or "Paciente"
        birth_date = patient.get("birth_date")
        custom_price = patient.get("custom_price")

        # Busca telefone via patient_contacts → contacts
        phone = ""
        patient_contacts = patient.get("patient_contacts") or []
        self_contact = next((pc for pc in patient_contacts if pc.get("is_self")), None)
        pc_row = self_contact or (patient_contacts[0] if patient_contacts else None)
        if pc_row:
            contact = pc_row.get("contacts") or {}
            phone = contact.get("phone") or ""

        doctor_display = DOCTOR_DISPLAY.get(appt.get("doctor_id", ""), "Médico")
        start_time = appt.get("start_time", "")
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            dt_br = dt.astimezone(timezone(timedelta(hours=-3)))
            data_hora = dt_br.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_hora = start_time[:16]

        if not appt.get("booking_fee_paid_at") and not appt.get("booking_fee_waived"):
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "start_time": start_time,
                "tipo": "taxa",
                "tipo_label": "Taxa de reserva",
                "valor": 100,
            })

        if not appt.get("paid_at"):
            valor = _calc_valor_consulta(
                appt.get("doctor_id", ""),
                birth_date,
                appt.get("consultation_type"),
                custom_price,
            )
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "start_time": start_time,
                "tipo": "consulta",
                "tipo_label": "Consulta",
                "valor": valor,
            })

    pendencias.sort(key=lambda x: x["start_time"])
    return pendencias


async def mark_paid(
    client,
    appointment_id: str,
    tipo: str,
    valor: int,
    forma_pagamento: str,
    paciente: str,
    medico: str,
    data_hora: str,
    phone: str,
) -> None:
    """Grava o pagamento no agendamento e tenta registrar na planilha/e-mail (best-effort).

    Assume que `tipo` já foi validado pelo chamador ("taxa" ou "consulta").
    """
    now = datetime.now(timezone.utc).isoformat()

    if tipo == "taxa":
        await client.from_("appointments").update({"booking_fee_paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "taxa_reserva"
    else:
        await client.from_("appointments").update({"paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "consulta"

    forma_label = FORMA_PAGAMENTO_LABEL.get(forma_pagamento, forma_pagamento)
    amount_str = str(valor)

    try:
        await _append_payment_sheet(
            patient_name=paciente,
            phone=phone,
            doctor_name=medico,
            appointment_dt=data_hora,
            amount=amount_str,
            payment_type=payment_type,
            payment_method=forma_pagamento,
        )
    except Exception:
        logger.exception("SHEETS_APPEND FAILED patient=%s", paciente)

    try:
        tipo_label = "Taxa de reserva" if tipo == "taxa" else "Consulta"
        await _send_clinic_email(
            subject=f"Pagamento registrado — {paciente}",
            body=(
                f"💰 Pagamento registrado pelo dashboard\n"
                f"Paciente: {paciente}\n"
                f"Médico: {medico}\n"
                f"Consulta: {data_hora}\n"
                f"Tipo: {tipo_label}\n"
                f"Valor: R$ {amount_str}\n"
                f"Forma: {forma_label}"
            ),
        )
    except Exception:
        logger.exception("EMAIL_FAILED patient=%s", paciente)
