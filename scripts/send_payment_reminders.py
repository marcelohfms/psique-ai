"""
Payment reminder and auto-cancellation script.
Runs every 30 minutes via GitHub Actions.

- At 2h after booking (if unpaid): send a friendly payment reminder
- At 4h after booking (if still unpaid): cancel the appointment and notify user

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

import httpx
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

UAZAPI_BASE_URL = os.environ.get("UAZAPI_BASE_URL", "https://psique.uazapi.com")
UAZAPI_TOKEN = os.environ.get("UAZAPI_TOKEN", "")

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def payment_reminder_message(first_name: str, doctor_label: str, date_str: str) -> str:
    return (
        f"Olá, {first_name}! 😊 Só passando para lembrar que sua consulta com "
        f"*{doctor_label}* no dia *{date_str}* ainda aguarda o pagamento da taxa "
        f"de reserva de R$ 100,00.\n\n"
        f"💳 PIX: 07670034467\n\n"
        f"Assim que o pagamento for realizado, sua vaga estará garantida! "
        f"Precisa de alguma ajuda ou tem alguma dúvida sobre o pagamento? É só me chamar aqui. 🙏"
    )


def payment_cancel_message(first_name: str, doctor_label: str, date_str: str) -> str:
    return (
        f"Olá, {first_name}. Infelizmente, como não recebemos o pagamento da taxa "
        f"de reserva da sua consulta com *{doctor_label}* no dia *{date_str}* dentro "
        f"do prazo de 4 horas, precisamos liberar a vaga. 😔\n\n"
        f"Caso queira reagendar, é só nos chamar aqui! "
        f"Ficaremos felizes em atendê-lo(a). 💙"
    )


async def send_whatsapp(phone: str, text: str) -> None:
    if not UAZAPI_TOKEN:
        print("  UAZAPI_TOKEN not set, skipping send.")
        return
    headers = {"token": UAZAPI_TOKEN, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{UAZAPI_BASE_URL}/send/text",
            json={"number": phone, "text": text},
            headers=headers,
        )


async def save_to_checkpoint(graph, phone: str, message: str, appt: dict) -> None:
    """Inject the message into the LangGraph checkpoint for this patient."""
    from langchain_core.messages import AIMessage

    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}

    snapshot = await graph.aget_state(config)
    update: dict = {"messages": [AIMessage(content=message)]}

    if not snapshot.values:
        user = appt.get("users") or {}
        patient_name = user.get("patient_name") or user.get("name") or "paciente"
        doctor_key = DOCTOR_KEYS.get(appt.get("doctor_id", ""), "")
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
    two_hours_ago = (now - timedelta(hours=2)).isoformat()
    four_hours_ago = (now - timedelta(hours=4)).isoformat()

    # ── Step 1: 2h reminder (not yet reminded, booked >= 2h ago) ──────────────
    reminder_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, created_at, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .is_("paid_at", "null")
        .is_("payment_reminder_sent_at", "null")
        .lte("created_at", two_hours_ago)
        .execute()
    )
    reminder_appts = reminder_result.data or []
    print(f"Appointments needing 2h payment reminder: {len(reminder_appts)}")

    # ── Step 2: 4h cancellation (reminder sent, still unpaid, booked >= 4h ago)
    cancel_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, created_at, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .is_("paid_at", "null")
        .not_.is_("payment_reminder_sent_at", "null")
        .lte("created_at", four_hours_ago)
        .execute()
    )
    cancel_appts = cancel_result.data or []
    print(f"Appointments to auto-cancel (unpaid after 4h): {len(cancel_appts)}")

    # Set up LangGraph checkpointer
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    checkpointer = None
    if conn_string:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        checkpointer = AsyncPostgresSaver.from_conn_string(conn_string)
        graph = build_graph(await checkpointer.__aenter__())
    else:
        print("SUPABASE_CONNECTION_STRING not set — messages won't be saved to LangGraph checkpoint.")

    try:
        # Process 2h reminders
        for appt in reminder_appts:
            appointment_id = appt["appointment_id"]
            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            date_str = start_dt.strftime("%d/%m/%Y às %H:%M")

            user = appt.get("users") or {}
            phone = user.get("number", "")
            patient_name = user.get("patient_name") or user.get("name") or "paciente"
            first_name = patient_name.split()[0]
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

            if not phone:
                continue

            message = payment_reminder_message(first_name, doctor_label, date_str)

            try:
                await send_whatsapp(phone, message)
                await client.from_("appointments").update({
                    "payment_reminder_sent_at": now.isoformat(),
                }).eq("appointment_id", appointment_id).execute()
                if graph:
                    await save_to_checkpoint(graph, phone, message, appt)
                print(f"  [payment_reminder] Sent to {phone} — {patient_name}")
            except Exception as e:
                print(f"  Failed to send reminder to {phone}: {e}")

        # Process 4h cancellations
        for appt in cancel_appts:
            appointment_id = appt["appointment_id"]
            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            date_str = start_dt.strftime("%d/%m/%Y às %H:%M")

            user = appt.get("users") or {}
            phone = user.get("number", "")
            patient_name = user.get("patient_name") or user.get("name") or "paciente"
            first_name = patient_name.split()[0]
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")
            doctor_id = appt.get("doctor_id", "")

            if not phone:
                continue

            message = payment_cancel_message(first_name, doctor_label, date_str)

            try:
                # Cancel Google Calendar event
                await cancel_calendar_event(appointment_id, doctor_id, client)

                # Update DB
                await client.from_("appointments").update({
                    "status": "canceled",
                    "updated_at": now.isoformat(),
                }).eq("appointment_id", appointment_id).execute()

                await send_whatsapp(phone, message)
                if graph:
                    await save_to_checkpoint(graph, phone, message, appt)
                print(f"  [payment_cancel] Canceled and notified {phone} — {patient_name}")
            except Exception as e:
                print(f"  Failed to cancel for {phone}: {e}")

    finally:
        if conn_string and checkpointer:
            await checkpointer.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
