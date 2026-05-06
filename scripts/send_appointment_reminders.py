"""
Send WhatsApp appointment reminders via Meta Cloud API templates.
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


def _template_components(first_name: str, doctor_label: str, time_str: str) -> list:
    return [{
        "type": "body",
        "parameters": [
            {"type": "text", "text": first_name},
            {"type": "text", "text": doctor_label},
            {"type": "text", "text": time_str},
        ],
    }]


async def send_reminder_template(phone: str, template_name: str, first_name: str, doctor_label: str, time_str: str) -> None:
    from app.whatsapp import send_template
    components = _template_components(first_name, doctor_label, time_str)
    await send_template(phone, template_name, "pt_BR", components)


def _plain_message(template_name: str, first_name: str, doctor_label: str, time_str: str) -> str:
    """Reconstruct the plain text to save into LangGraph checkpoint."""
    if template_name == "lembrete_dia_anterior":
        return (
            f"Olá! Lembrete da Psiquê: {first_name} tem consulta amanhã "
            f"com {doctor_label} às {time_str}. Consegue confirmar a presença?"
        )
    return (
        f"Bom dia! Hoje é o dia da consulta de {first_name} "
        f"com {doctor_label} às {time_str}. Estamos esperando na Psiquê!"
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
            first_name = patient_name.split()[0] if patient_name else "paciente"
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

            if not phone:
                continue

            if appt_date == tomorrow_str:
                event_type = "reminder_day_before"
                template_name = "lembrete_dia_anterior"
            elif appt_date == today_str:
                event_type = "reminder_day_of"
                template_name = "lembrete_dia_consulta"
            else:
                continue

            if await already_sent(client, appointment_id, event_type):
                print(f"  Already sent {event_type} for {appointment_id}, skipping.")
                continue

            try:
                await send_reminder_template(phone, template_name, first_name, doctor_label, time_str)
                await log_reminder(client, phone, appointment_id, event_type)
                message = _plain_message(template_name, first_name, doctor_label, time_str)
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
