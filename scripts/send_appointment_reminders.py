"""
Send WhatsApp appointment reminders.
Runs daily at 8h (Recife time) via GitHub Actions.

- Day before: asks patient to confirm the appointment
- Day of:     reminds patient the appointment is today

After sending, the reminder message is saved to the LangGraph checkpoint
so the LLM has full conversation context when the patient replies.
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


async def send_whatsapp(phone: str, text: str) -> None:
    if not UAZAPI_TOKEN:
        print("  UAZAPI_TOKEN not set, skipping send.")
        return
    url = f"{UAZAPI_BASE_URL}/send/text"
    headers = {"token": UAZAPI_TOKEN, "Content-Type": "application/json"}
    payload = {"number": phone, "text": text}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload, headers=headers)


def day_before_message(patient_name: str, doctor_label: str, time_str: str) -> str:
    first_name = patient_name.split()[0] if patient_name else "paciente"
    return (
        f"Olá! 👋 Lembrete da Clínica Psique: *{first_name}* tem consulta amanhã "
        f"com *{doctor_label}* às *{time_str}*. "
        f"Consegue confirmar a presença pra gente?"
    )


def day_of_message(patient_name: str, doctor_label: str, time_str: str) -> str:
    first_name = patient_name.split()[0] if patient_name else "paciente"
    return (
        f"Bom dia! ☀️ Hoje é o dia da consulta de *{first_name}* "
        f"com *{doctor_label}* às *{time_str}*. "
        f"Estamos te esperando na Clínica Psique! 💙"
    )


async def already_sent(client, appointment_id: str, event_type: str) -> bool:
    result = await (
        client.from_("events")
        .select("id")
        .eq("event_type", event_type)
        .eq("metadata->>appointment_id", appointment_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def log_reminder(client, phone: str, appointment_id: str, event_type: str) -> None:
    try:
        await client.from_("events").insert({
            "event_type": event_type,
            "phone": phone,
            "metadata": {"appointment_id": appointment_id},
        }).execute()
    except Exception:
        pass


async def save_to_checkpoint(graph, phone: str, message: str, appt: dict) -> None:
    """Inject the reminder message into the LangGraph checkpoint for this patient."""
    from langchain_core.messages import AIMessage

    # UAZAPI uses bare numbers; LangGraph thread_id uses the WhatsApp JID format
    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}

    snapshot = await graph.aget_state(config)
    update: dict = {"messages": [AIMessage(content=message)]}

    if not snapshot.values:
        # No existing conversation — initialise basic state so the graph
        # routes correctly when the patient replies.
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


async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    now = datetime.now(TZ)
    today_str = now.date().isoformat()
    tomorrow_str = (now.date() + timedelta(days=1)).isoformat()
    day_after_tomorrow_str = (now.date() + timedelta(days=2)).isoformat()

    # Fetch scheduled appointments for today and tomorrow
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .gte("start_time", f"{today_str}T00:00:00")
        .lt("start_time", f"{day_after_tomorrow_str}T00:00:00")
        .execute()
    )

    appointments = result.data or []
    print(f"Found {len(appointments)} appointment(s) for today/tomorrow.")

    # Set up LangGraph checkpointer if connection string is available
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    if conn_string:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        checkpointer = AsyncPostgresSaver.from_conn_string(conn_string)
        graph = build_graph(await checkpointer.__aenter__())
    else:
        print("SUPABASE_CONNECTION_STRING not set — reminders won't be saved to LangGraph checkpoint.")

    try:
        for appt in appointments:
            appointment_id = appt["appointment_id"]
            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            appt_date = start_dt.date().isoformat()
            time_str = start_dt.strftime("%H:%M")

            user = appt.get("users") or {}
            phone = user.get("number", "")
            patient_name = user.get("patient_name") or user.get("name") or "paciente"
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

            if not phone:
                continue

            if appt_date == tomorrow_str:
                event_type = "reminder_day_before"
                message = day_before_message(patient_name, doctor_label, time_str)
            elif appt_date == today_str:
                event_type = "reminder_day_of"
                message = day_of_message(patient_name, doctor_label, time_str)
            else:
                continue

            # Skip if already sent
            if await already_sent(client, appointment_id, event_type):
                print(f"  Already sent {event_type} for {appointment_id}, skipping.")
                continue

            try:
                await send_whatsapp(phone, message)
                await log_reminder(client, phone, appointment_id, event_type)
                if graph:
                    await save_to_checkpoint(graph, phone, message, appt)
                print(f"  [{event_type}] Sent to {phone} — {patient_name} @ {time_str}")
            except Exception as e:
                print(f"  Failed to send to {phone}: {e}")
    finally:
        if conn_string and graph:
            await checkpointer.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
