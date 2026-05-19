"""
Mark past appointments as 'completed' and send a post-consultation WhatsApp template.
Runs via GitHub Actions on a schedule (every hour).

Processes appointments where:
  - status = 'scheduled'
  - end_time < now() - 1h  (at least 1 hour has passed since the appointment ended)
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()


async def send_pos_consulta(phone: str, first_name: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    content = (
        f"Olá! Esperamos que a consulta de {first_name} tenha sido boa! "
        f"Aproveite para agendar a próxima — a continuidade do tratamento faz toda a diferença. "
        f"Fique à vontade para responder pelo WhatsApp quando quiser."
    )
    await send_template_message(
        conv_id,
        template_name="pos_consulta",
        language="pt_BR",
        category="MARKETING",
        body_params={"1": first_name},
        content=content,
    )


async def main():
    from supabase import acreate_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    result = await (
        client.from_("appointments")
        .select("id, appointment_id, end_time, user_id, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .lt("end_time", cutoff)
        .execute()
    )

    appointments = result.data or []
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0

    for appt in appointments:
        await (
            client.from_("appointments")
            .update({"status": "completed", "updated_at": now_iso})
            .eq("id", appt["id"])
            .execute()
        )

        user = appt.get("users") or {}
        phone = user.get("number", "")
        patient_name = user.get("patient_name") or user.get("name") or "paciente"
        first_name = patient_name.split()[0] if patient_name else "paciente"

        if phone:
            try:
                await send_pos_consulta(phone, first_name)
                print(f"Message sent to {phone} for appointment {appt['appointment_id']}")
            except Exception as e:
                print(f"Failed to send message to {phone}: {e}")

        count += 1

    print(f"Marked {count} appointment(s) as completed.")


if __name__ == "__main__":
    asyncio.run(main())
