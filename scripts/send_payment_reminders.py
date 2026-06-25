"""
Payment reminder and auto-cancellation script.
Runs every 30 minutes via GitHub Actions.

Regras:
- Só executa entre 7h e 23h (horário de Recife). Fora desse intervalo, encerra sem fazer nada.
- 2h após o agendamento (se não pago): envia lembrete de pagamento via WhatsApp.
  O lembrete é enviado a TODOS os contatos com role 'financeiro' vinculados ao paciente.
  Se o envio falhar, não marca como enviado (a próxima execução tentará de novo).
- 2h após o lembrete (se ainda não pago): tenta enviar aviso de cancelamento.
  O cancelamento SÓ ocorre se o aviso for entregue com sucesso via WhatsApp.
  Se o envio falhar, a consulta NÃO é cancelada e a próxima execução tentará de novo.

Both messages are saved to the LangGraph checkpoint so the LLM has full
conversation context when the patient replies.

Requires in Supabase:
  ALTER TABLE appointments ADD COLUMN IF NOT EXISTS paid_at timestamptz;
  ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_reminder_sent_at timestamptz;
"""
import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def payment_reminder_message(contact_first_name: str, doctor_label: str, date_str: str, patient_first_name: str | None = None) -> str:
    consulta = f"a consulta de *{patient_first_name}*" if patient_first_name else "sua consulta"
    return (
        f"Olá, {contact_first_name}! 😊 Só passando para lembrar que {consulta} com "
        f"*{doctor_label}* no dia *{date_str}* ainda aguarda o pagamento da taxa "
        f"de reserva de R$ 100,00.\n\n"
        f"💳 PIX: {os.environ.get('PIX_KEY', '42006848000178')}\n\n"
        f"Assim que o pagamento for realizado, a vaga estará garantida! "
        f"Precisa de alguma ajuda ou tem alguma dúvida sobre o pagamento? É só me chamar aqui. 🙏"
    )


def payment_cancel_message(contact_first_name: str, doctor_label: str, date_str: str, patient_first_name: str | None = None) -> str:
    if patient_first_name:
        consulta_ref = f"da consulta de *{patient_first_name}* com *{doctor_label}* no dia *{date_str}*"
    else:
        consulta_ref = f"da sua consulta com *{doctor_label}* no dia *{date_str}*"
    return (
        f"Olá, {contact_first_name}. Infelizmente, como não recebemos o pagamento da taxa "
        f"de reserva {consulta_ref} dentro "
        f"do prazo de 4 horas, precisamos liberar a vaga. 😔\n\n"
        f"Caso queira reagendar, é só nos chamar aqui! "
        f"Ficaremos felizes em atendê-lo(a). 💙"
    )


async def send_whatsapp(phone: str, text: str) -> None:
    from app.whatsapp import send_text
    phone_fmt = f"{phone}@s.whatsapp.net" if "@" not in phone else phone
    await send_text(phone_fmt, text)


async def get_financial_contacts(client, patient_id: str) -> list[dict]:
    """Return all contacts with role 'financeiro' for a patient (phone + name)."""
    result = await (
        client.from_("patient_contacts")
        .select("role, contacts(phone, name)")
        .eq("patient_id", patient_id)
        .eq("role", "financeiro")
        .execute()
    )
    contacts = []
    for row in result.data or []:
        c = row.get("contacts") or {}
        if c.get("phone"):
            contacts.append({"phone": c["phone"], "name": c.get("name", "")})
    return contacts


async def save_to_checkpoint(graph, phone: str, message: str, patient_name: str, doctor_key: str) -> None:
    """Inject the message into the LangGraph checkpoint for this contact."""
    from langchain_core.messages import AIMessage

    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}

    snapshot = await graph.aget_state(config)
    update: dict = {"messages": [AIMessage(content=message)]}

    if not snapshot.values:
        update.update({
            "phone": thread_phone,
            "stage": "patient_agent",
            "user_name": patient_name,
            "patient_name": patient_name,
            "is_patient": True,
            "preferred_doctor": doctor_key,
        })

    await graph.aupdate_state(config, update, as_node="patient_agent")


async def cancel_calendar_event(appointment_id: str, doctor_id: str, supabase_client) -> None:
    """Cancel the Google Calendar event for this appointment."""
    try:
        result = await (
            supabase_client.from_("doctors")
            .select("agenda_id")
            .eq("doctor_id", doctor_id)
            .single()
            .execute()
        )
        calendar_id = result.data.get("agenda_id") if result.data else None
        if not calendar_id:
            print(f"  No calendar_id for doctor {doctor_id}, skipping Calendar cancel.")
            return

        from app.google_calendar import cancel_event
        await cancel_event(calendar_id, appointment_id)
        print(f"  Calendar event {appointment_id} canceled.")
    except Exception as e:
        print(f"  Calendar cancel failed (non-fatal): {e}")


async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    now = datetime.now(TZ)

    # ── Janela de envio: apenas entre 7h e 23h (horário de Recife) ────────────
    WINDOW_START = 7
    WINDOW_END = 23
    if not (WINDOW_START <= now.hour < WINDOW_END):
        print(f"Fora da janela de envio ({WINDOW_START}h–{WINDOW_END}h). Encerrando.")
        return

    two_hours_ago = (now - timedelta(hours=2)).isoformat()

    _appt_select = (
        "appointment_id, start_time, doctor_id, created_at, payment_reminder_sent_at, "
        "patient_id, patients(name)"
    )

    # ── Step 1: 1st reminder (not yet reminded, booked >= 2h ago) ─────────────
    reminder_result = await (
        client.from_("appointments")
        .select(_appt_select)
        .eq("status", "scheduled")
        .eq("booking_fee_waived", False)
        .is_("booking_fee_paid_at", "null")
        .is_("payment_reminder_sent_at", "null")
        .lte("created_at", two_hours_ago)
        .gte("start_time", now.isoformat())
        .execute()
    )
    reminder_appts = reminder_result.data or []
    print(f"Appointments needing payment reminder: {len(reminder_appts)}")

    # ── Step 2: cancellation (reminder sent >= 2h ago, still unpaid) ──────────
    cancel_result = await (
        client.from_("appointments")
        .select(_appt_select)
        .eq("status", "scheduled")
        .eq("booking_fee_waived", False)
        .is_("booking_fee_paid_at", "null")
        .not_.is_("payment_reminder_sent_at", "null")
        .lte("payment_reminder_sent_at", two_hours_ago)
        .gte("start_time", now.isoformat())
        .execute()
    )
    cancel_appts = cancel_result.data or []
    print(f"Appointments to auto-cancel (unpaid 2h after reminder): {len(cancel_appts)}")

    # Set up LangGraph checkpointer
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        pg_conn = await AsyncConnection.connect(
            conn_string,
            autocommit=True,
            prepare_threshold=None,
            row_factory=dict_row,
        )
        checkpointer = AsyncPostgresSaver(pg_conn)
        graph = build_graph(checkpointer=checkpointer)
    else:
        print("SUPABASE_CONNECTION_STRING not set — messages won't be saved to LangGraph checkpoint.")

    try:
        # ── Process 2h reminders ──────────────────────────────────────────────
        for appt in reminder_appts:
            appointment_id = appt["appointment_id"]
            patient_id = appt.get("patient_id")
            if not patient_id:
                print(f"  [payment_reminder] Skipping {appointment_id} — no patient_id")
                continue

            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            date_str = start_dt.strftime("%d/%m/%Y às %H:%M")
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")
            doctor_key = DOCTOR_KEYS.get(appt.get("doctor_id", ""), "")
            patient_name = (appt.get("patients") or {}).get("name", "paciente")

            financial_contacts = await get_financial_contacts(client, patient_id)
            if not financial_contacts:
                print(f"  [payment_reminder] No financial contacts for patient_id={patient_id}")
                continue

            any_sent = False
            for contact in financial_contacts:
                phone = contact["phone"]
                contact_first = (contact["name"] or patient_name).split()[0]
                # Show patient name separately only when contact and patient differ
                patient_first = patient_name.split()[0] if contact["name"] and contact["name"] != patient_name else None
                message = payment_reminder_message(contact_first, doctor_label, date_str, patient_first)
                try:
                    await send_whatsapp(phone, message)
                    any_sent = True
                    print(f"  [payment_reminder] Sent to {phone} — {patient_name}")
                except Exception as e:
                    print(f"  [payment_reminder] Failed to send to {phone}: {e}")
                if graph:
                    try:
                        await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
                    except Exception as e:
                        print(f"  [payment_reminder] save_to_checkpoint failed (non-fatal): {e}")

            if any_sent:
                try:
                    await client.from_("appointments").update({
                        "payment_reminder_sent_at": now.isoformat(),
                    }).eq("appointment_id", appointment_id).execute()
                except Exception as e:
                    print(f"  [payment_reminder] DB update failed for {appointment_id}: {e}")

        # ── Process 4h cancellations ──────────────────────────────────────────
        for appt in cancel_appts:
            appointment_id = appt["appointment_id"]
            patient_id = appt.get("patient_id")
            if not patient_id:
                print(f"  [payment_cancel] Skipping {appointment_id} — no patient_id")
                continue

            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            date_str = start_dt.strftime("%d/%m/%Y às %H:%M")
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")
            doctor_key = DOCTOR_KEYS.get(appt.get("doctor_id", ""), "")
            doctor_id = appt.get("doctor_id", "")
            patient_name = (appt.get("patients") or {}).get("name", "paciente")

            financial_contacts = await get_financial_contacts(client, patient_id)
            if not financial_contacts:
                print(f"  [payment_cancel] No financial contacts for patient_id={patient_id}")
                continue

            # Must notify at least one contact before canceling
            any_notified = False
            for contact in financial_contacts:
                phone = contact["phone"]
                contact_first = (contact["name"] or patient_name).split()[0]
                patient_first = patient_name.split()[0] if contact["name"] and contact["name"] != patient_name else None
                message = payment_cancel_message(contact_first, doctor_label, date_str, patient_first)
                try:
                    await send_whatsapp(phone, message)
                    any_notified = True
                    print(f"  [payment_cancel] WhatsApp enviado para {phone}.")
                except Exception as e:
                    print(f"  [payment_cancel] WhatsApp FALHOU para {phone}: {e}")
                if graph:
                    try:
                        await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
                    except Exception as e:
                        print(f"  [payment_cancel] save_to_checkpoint failed (non-fatal): {e}")

            if not any_notified:
                print(f"  [payment_cancel] Nenhum contato notificado — cancelamento adiado para {appointment_id}")
                continue

            # Cancel Google Calendar event
            await cancel_calendar_event(appointment_id, doctor_id, client)

            # Update DB status
            try:
                await client.from_("appointments").update({
                    "status": "canceled",
                    "updated_at": now.isoformat(),
                }).eq("appointment_id", appointment_id).execute()
            except Exception as e:
                print(f"  [payment_cancel] DB update failed for {appointment_id}: {e}")

            # Notify clinic by email
            try:
                from app.email_sender import send_clinic_notification_email
                import asyncio as _asyncio
                subject = f"Consulta cancelada por falta de pagamento — {patient_name}"
                body = (
                    f"A consulta abaixo foi cancelada automaticamente por falta de pagamento da taxa de reserva.\n\n"
                    f"Paciente: {patient_name}\n"
                    f"Médico(a): {doctor_label}\n"
                    f"Data/hora: {date_str}\n"
                    f"Contatos notificados: {', '.join(c['phone'] for c in financial_contacts)}\n\n"
                    f"A vaga foi liberada no Google Calendar."
                )
                for attempt in range(1, 3):
                    try:
                        await send_clinic_notification_email(subject, body)
                        print(f"  [payment_cancel] Clinic notification email sent (attempt {attempt}).")
                        break
                    except Exception as e:
                        print(f"  [payment_cancel] Clinic email attempt {attempt} failed: {e}")
                        if attempt < 2:
                            await _asyncio.sleep(5)
            except Exception as e:
                print(f"  [payment_cancel] Clinic email setup failed: {e}")

            print(f"  [payment_cancel] Canceled {'and notified' if any_notified else '(WhatsApp FAILED)'} — {patient_name}")

    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
