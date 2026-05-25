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
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    plain = _plain_message(template_name, first_name, doctor_label, time_str)
    await send_template_message(
        conv_id,
        template_name=template_name,
        language="pt_BR",
        category="UTILITY",
        body_params={"1": first_name, "2": doctor_label, "3": time_str},
        content=plain,
    )


def _plain_message(template_name: str, first_name: str, doctor_label: str, time_str: str, modality: str = "") -> str:
    is_online = modality == "online"
    if template_name in ("lembrete_dia_anteior", "lembrete_dia_anterior_online"):
        if is_online:
            return (
                f"Olá! Lembrete da Psiquê: {first_name} tem consulta online amanhã "
                f"com {doctor_label} às {time_str}. Consegue confirmar a presença?"
            )
        return (
            f"Olá! Lembrete da Psiquê: {first_name} tem consulta amanhã "
            f"com {doctor_label} às {time_str}. Consegue confirmar a presença?"
        )
    # Day-of reminder
    if is_online:
        return (
            f"Olá! 🙂\n"
            f"Hoje é o dia da consulta online de {first_name} com {doctor_label} às {time_str}.\n\n"
            f"A consulta é online - você receberá o link da consulta no horário agendado."
        )
    return (
        f"Bom dia! Hoje é o dia da consulta de {first_name} "
        f"com {doctor_label} às {time_str}. Estamos esperando na Psiquê! 😊"
    )


async def save_to_checkpoint(graph, phone: str, message: str, appt: dict) -> None:
    from langchain_core.messages import AIMessage
    from app.database import save_message

    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}

    user = appt.get("users") or {}
    patient_name = user.get("patient_name") or user.get("name") or "paciente"
    doctor_key = DOCTOR_KEYS.get(appt.get("doctor_id", ""), "")

    # Sempre garante que stage e campos do usuário estão corretos no checkpoint,
    # independentemente de ser novo ou existente. Isso evita que o roteamento
    # vá para collect_info quando o paciente responder ao lembrete.
    update: dict = {
        "messages": [AIMessage(content=message)],
        "phone": thread_phone,
        "stage": "patient_agent",
        "user_name": patient_name,
        "patient_name": patient_name,
        "is_patient": True,
        "preferred_doctor": doctor_key,
    }

    await graph.aupdate_state(config, update, as_node="patient_agent")

    # Salva também na tabela messages do Supabase para garantir contexto
    # mesmo que o checkpoint PostgreSQL falhe.
    await save_message(thread_phone, "assistant", message)


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
    # Only remind patients who booked at least 12h ago (avoid reminding same-day bookings)
    day_before_appts = []
    if 7 <= now.hour < 20:
        booked_before = (now - timedelta(hours=12)).isoformat()
        result = await (
            client.from_("appointments")
            .select("appointment_id, start_time, doctor_id, modality, users(number, patient_name, name)")
            .eq("status", "scheduled")
            .is_("reminder_day_before_sent_at", "null")
            .gte("start_time", f"{tomorrow_start}T00:00:00")
            .lt("start_time", f"{tomorrow_end}T00:00:00")
            .lte("created_at", booked_before)
            .execute()
        )
        day_before_appts = result.data or []

    # ── Day-of reminders: send at 08h or 2h before, whichever comes first ────
    # Fetch all today's future scheduled appointments not yet reminded
    day_of_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, modality, users(number, patient_name, name)")
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
            modality = appt.get("modality") or ""

            user = appt.get("users") or {}
            phone = user.get("number", "")
            patient_name = user.get("patient_name") or user.get("name") or "paciente"
            first_name = patient_name.split()[0] if patient_name else "paciente"
            doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

            if not phone:
                continue

            # Use online-specific template name for online appointments
            effective_template = template_name
            if modality == "online":
                effective_template = template_name.replace("lembrete_dia_consulta", "lembrete_dia_consulta_online")

            try:
                await send_reminder_template(phone, effective_template, first_name, doctor_label, time_str)
                await client.from_("appointments").update({
                    sent_col: now.isoformat(),
                }).eq("appointment_id", appointment_id).execute()
                message = _plain_message(effective_template, first_name, doctor_label, time_str, modality)
                if graph:
                    await save_to_checkpoint(graph, phone, message, appt)
                print(f"  [{effective_template}] Sent to {phone} — {patient_name} @ {time_str} [{modality or 'sem modalidade'}]")
            except Exception as e:
                print(f"  Failed to send to {phone}: {e}")
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
