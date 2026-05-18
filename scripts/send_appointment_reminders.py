"""
Send WhatsApp appointment reminders via Meta Cloud API templates.
Runs every 30 minutes via GitHub Actions.

- Day before: sends between 07h-20h Recife the day before the appointment
- Day of:     sends up to 2h before the appointment starts (never after)

After sending, the reminder timestamp is saved directly on the appointment row
(same pattern as payment_reminder_sent_at).

Requires in Supabase:
  ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_day_before_sent_at timestamptz;
  ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_day_of_sent_at timestamptz;
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
    if template_name == "lembrete_dia_anteior":
        return (
            f"Olá! Lembrete da Psiquê: {first_name} tem consulta amanhã "
            f"com {doctor_label} às {time_str}. Consegue confirmar a presença?"
        )
    return (
        f"Bom dia! Hoje é o dia da consulta de {first_name} "
        f"com {doctor_label} às {time_str}. Estamos esperando na Psiquê!"
    )


async def save_to_checkpoint(graph, phone: str, message: str, appt: dict) -> None:
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
    tomorrow_start = (now.date() + timedelta(days=1)).isoformat()
    tomorrow_end = (now.date() + timedelta(days=2)).isoformat()
    today_start = now.date().isoformat()
    two_hours_later = now + timedelta(hours=2)

    # ── Day-before reminders: send between 07h-20h Recife ────────────────────
    day_before_appts = []
    if 7 <= now.hour < 20:
        result = await (
            client.from_("appointments")
            .select("appointment_id, start_time, doctor_id, users(number, patient_name, name)")
            .eq("status", "scheduled")
            .is_("reminder_day_before_sent_at", "null")
            .gte("start_time", f"{tomorrow_start}T00:00:00")
            .lt("start_time", f"{tomorrow_end}T00:00:00")
            .execute()
        )
        day_before_appts = result.data or []

    # ── Day-of reminders: send at 08h or 2h before, whichever comes first ────
    # Fetch all today's future scheduled appointments not yet reminded
    day_of_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .is_("reminder_day_of_sent_at", "null")
        .gt("start_time", now.isoformat())
        .gte("start_time", f"{today_start}T00:00:00")
        .lt("start_time", f"{tomorrow_start}T00:00:00")
        .execute()
    )
    # Send if it's already 08h+ OR the appointment starts within 2h
    day_of_appts = [
        a for a in (day_of_result.data or [])
        if now.hour >= 8
        or datetime.fromisoformat(a["start_time"]).astimezone(TZ) <= two_hours_later
    ]

    print(f"Day-before reminders to send: {len(day_before_appts)}")
    print(f"Day-of reminders to send: {len(day_of_appts)}")

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
        print("SUPABASE_CONNECTION_STRING not set — reminders won't be saved to LangGraph checkpoint.")

    try:
        batch = [
            *((a, "lembrete_dia_anteior", "reminder_day_before_sent_at") for a in day_before_appts),
            *((a, "lembrete_dia_consulta", "reminder_day_of_sent_at") for a in day_of_appts),
        ]

        for appt, template_name, sent_col in batch:
            appointment_id = appt["appointment_id"]
            start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
            time_str = start_dt.strftime("%H:%M")

            user = appt.get("users") or {}
            phone = user.get("number", "")
            patient_name = user.get("patient_name") or user.get("name") or "paciente"
            first_name = patient_name.split()[0] if patient_name else "paciente"
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

            if not phone:
                continue

            try:
                await send_reminder_template(phone, template_name, first_name, doctor_label, time_str)
                await client.from_("appointments").update({
                    sent_col: now.isoformat(),
                }).eq("appointment_id", appointment_id).execute()
                message = _plain_message(template_name, first_name, doctor_label, time_str)
                if graph:
                    await save_to_checkpoint(graph, phone, message, appt)
                # Mirror message to Chatwoot so agents can see it
                try:
                    from app.chatwoot import find_or_create_conversation, send_message
                    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
                    conv_id = await find_or_create_conversation(phone_wpp)
                    await send_message(conv_id, message)
                except Exception as cw_err:
                    print(f"  Chatwoot mirror failed for {phone}: {cw_err}")
                print(f"  [{template_name}] Sent to {phone} — {patient_name} @ {time_str}")
            except Exception as e:
                print(f"  Failed to send to {phone}: {e}")
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
