"""
Script de disparo manual do template de reagendamento.

Uso:
  uv run python scripts/reschedule_notify.py \
    --phone 5583999999999 \
    --appointment-id <uuid> \
    --new-start 2026-06-16T14:00:00-03:00 \
    --new-end   2026-06-16T15:00:00-03:00
"""
import argparse
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from app.whatsapp import send_template
except Exception:
    send_template = None  # type: ignore

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "julio": "Dr. Júlio",
    "bruna": "Dra. Bruna",
}

_WEEKDAYS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]


def _format_template_vars(
    patient_name: str,
    doctor_label: str,
    original_start: datetime,
    suggested_start: datetime,
) -> dict:
    def _fmt_date(dt: datetime) -> str:
        day_name = _WEEKDAYS_PT[dt.weekday()]
        return f"{day_name}, {dt.strftime('%d/%m')}"

    def _fmt_time(dt: datetime) -> str:
        if dt.minute == 0:
            return f"{dt.hour}h"
        return dt.strftime("%Hh%M")

    return {
        "patient_name": patient_name,
        "doctor_label": doctor_label,
        "original_date": _fmt_date(original_start),
        "original_time": _fmt_time(original_start),
        "suggested_date": _fmt_date(suggested_start),
        "suggested_time": _fmt_time(suggested_start),
    }


async def send_reschedule_template(
    phone: str,
    patient_name: str,
    doctor_label: str,
    original_date: str,
    original_time: str,
    suggested_date: str,
    suggested_time: str,
) -> None:
    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": patient_name},
                {"type": "text", "text": doctor_label},
                {"type": "text", "text": original_date},
                {"type": "text", "text": original_time},
                {"type": "text", "text": suggested_date},
                {"type": "text", "text": suggested_time},
            ],
        }
    ]
    await send_template(phone, "reagendamento", "pt_BR", components)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Envia template de reagendamento ao paciente.")
    parser.add_argument("--phone", required=True, help="Telefone internacional sem +")
    parser.add_argument("--appointment-id", required=True, dest="appointment_id")
    parser.add_argument("--new-start", required=True, dest="new_start", help="ISO 8601 com fuso")
    parser.add_argument("--new-end", required=True, dest="new_end", help="ISO 8601 com fuso")
    args = parser.parse_args()

    from app.database import get_supabase, DOCTOR_NAMES

    db = await get_supabase()

    appt_result = await (
        db.from_("appointments")
        .select("user_id, start_time, doctor_id")
        .eq("appointment_id", args.appointment_id)
        .single()
        .execute()
    )
    appt = appt_result.data
    if not appt:
        raise SystemExit(f"Agendamento não encontrado: {args.appointment_id}")

    user_result = await (
        db.from_("users")
        .select("name, patient_name")
        .eq("id", appt["user_id"])
        .single()
        .execute()
    )
    user = user_result.data
    if not user:
        raise SystemExit(f"Usuário não encontrado para o agendamento {args.appointment_id}")

    patient_name = user.get("patient_name") or user.get("name") or "Paciente"
    doctor_key = DOCTOR_NAMES.get(appt.get("doctor_id", ""), "")
    doctor_label = DOCTOR_LABELS.get(doctor_key, "médico(a)")

    original_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    suggested_start = datetime.fromisoformat(args.new_start).astimezone(TZ)
    suggested_end = datetime.fromisoformat(args.new_end).astimezone(TZ)

    vars_ = _format_template_vars(patient_name, doctor_label, original_start, suggested_start)

    await send_reschedule_template(
        phone=args.phone,
        patient_name=vars_["patient_name"],
        doctor_label=vars_["doctor_label"],
        original_date=vars_["original_date"],
        original_time=vars_["original_time"],
        suggested_date=vars_["suggested_date"],
        suggested_time=vars_["suggested_time"],
    )

    await db.from_("users").update({
        "pending_reschedule": {
            "appointment_id": args.appointment_id,
            "suggested_start": suggested_start.isoformat(),
            "suggested_end": suggested_end.isoformat(),
        }
    }).eq("id", appt["user_id"]).execute()

    print(f"✅ Template enviado para {args.phone}. pending_reschedule gravado.")


if __name__ == "__main__":
    asyncio.run(main())
