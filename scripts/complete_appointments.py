"""
Mark past appointments as 'completed' and send a post-consultation WhatsApp message.
Runs via GitHub Actions on a schedule (every hour).

Processes appointments where:
  - status = 'scheduled'
  - end_time < now() - 1h  (at least 1 hour has passed since the appointment ended)
"""
import asyncio
import os
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
load_dotenv()

UAZAPI_BASE_URL = os.environ.get("UAZAPI_BASE_URL", "https://psique.uazapi.com")
UAZAPI_TOKEN = os.environ.get("UAZAPI_TOKEN", "")


async def send_whatsapp(phone: str, text: str) -> None:
    """Send a WhatsApp message via UAZAPI."""
    if not UAZAPI_TOKEN:
        return
    url = f"{UAZAPI_BASE_URL}/send/text"
    headers = {"token": UAZAPI_TOKEN, "Content-Type": "application/json"}
    payload = {"number": phone, "text": text}
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(url, json=payload, headers=headers)


def build_message(patient_name: str) -> str:
    first_name = patient_name.split()[0] if patient_name else "paciente"
    return (
        f"Olá! 😊 Esperamos que a consulta de *{first_name}* tenha sido boa!\n\n"
        f"Caso tenha sido prescrita alguma medicação, lembre-se de que a receita tem validade "
        f"limitada — especialmente se for de uso contínuo, pode ser necessário renová-la antes do vencimento.\n\n"
        f"Aproveitando, já gostaria de agendar a próxima consulta? Garantir a continuidade "
        f"do tratamento faz toda a diferença! 🗓️\n\n"
        f"Fique à vontade para responder pelo WhatsApp quando quiser. Estou à disposição! 💙"
    )


async def main():
    from supabase import acreate_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)

    # Only process appointments that ended at least 1 hour ago
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
        # Mark as completed
        await (
            client.from_("appointments")
            .update({"status": "completed", "updated_at": now_iso})
            .eq("id", appt["id"])
            .execute()
        )

        # Send post-consultation WhatsApp message
        user = appt.get("users") or {}
        phone = user.get("number", "")
        patient_name = user.get("patient_name") or user.get("name") or "paciente"

        if phone:
            message = build_message(patient_name)
            try:
                await send_whatsapp(phone, message)
                print(f"Message sent to {phone} for appointment {appt['appointment_id']}")
            except Exception as e:
                print(f"Failed to send message to {phone}: {e}")

        count += 1

    print(f"Marked {count} appointment(s) as completed.")


if __name__ == "__main__":
    asyncio.run(main())
