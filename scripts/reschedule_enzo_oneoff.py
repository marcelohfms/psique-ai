"""
One-off: reagenda os dois slots do Enzo Fernandes para 28/05/2026 às 16h e 17h (Dr. Júlio).
Uso: uv run python scripts/reschedule_enzo_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")

# Slots atuais (20/05) → novos slots (28/05)
APPOINTMENTS = [
    {
        "appointment_id": "MTM4ZGtuOXF2bHVpcTJqN3FpdHNpc2FwbDEgZHIuanVsaW9nb3V2ZWlhQG0",
        "new_start": datetime(2026, 5, 28, 16, 0, tzinfo=TZ),
        "label": "16h",
    },
    {
        "appointment_id": "MmZuNnNob3Y1NTJidDV1MGZhcW9naW5naTMgZHIuanVsaW9nb3V2ZWlhQG0",
        "new_start": datetime(2026, 5, 28, 17, 0, tzinfo=TZ),
        "label": "17h",
    },
]

PATIENT_NAME = "Enzo Fernandes Cândido Araújo"
DOCTOR_LABEL = "Dr. Júlio"
PHONE = "5581996773325@s.whatsapp.net"

OLD_DATETIME = "20/05/2026 às 09:00 e 11:00"
NEW_DATETIME  = "28/05/2026 às 16:00 e 17:00"


async def main():
    from app.graph.tools import _notify_clinic

    message = (
        f"Agendamento alterado! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Horário anterior: {OLD_DATETIME}\n"
        f"Novo horário: {NEW_DATETIME}\n"
        f"Médico(a): {DOCTOR_LABEL}"
    )

    print(f"📨 Enviando notificação de reagendamento...")
    print(f"   {message}")
    await _notify_clinic(
        message,
        phone=PHONE,
        subject=f"Agendamento alterado — {PATIENT_NAME}",
    )
    print("✅ Notificação enviada.")


if __name__ == "__main__":
    asyncio.run(main())
