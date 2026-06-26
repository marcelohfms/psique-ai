"""
Libera slots de consultas cujo reagendamento ficou pendente por mais de 1 hora.

Fluxo:
- Busca appointments com reschedule_requested_at IS NOT NULL e status=scheduled
  onde reschedule_requested_at <= now() - 1h E última mensagem do paciente > 1h atrás
- Cancela o evento no Google Calendar (libera o slot)
- Muda status para pending_reschedule (preserva booking_fee_paid_at, paid_at, etc.)
- Envia mensagem ao paciente informando que a vaga foi liberada e ele pode remarcar a qualquer hora

Quando o paciente voltar e confirmar novo horário:
- reschedule_appointment cria novo evento no Calendar com o horário escolhido
- Atualiza start_time, end_time, status=scheduled, reschedule_requested_at=null

Executa a cada 30 minutos via GitHub Actions.
Só roda entre 7h e 23h (horário de Recife).
"""
import asyncio
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
WINDOW_START = 7
WINDOW_END = 23
TIMEOUT_MINUTES = 60

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def pending_reschedule_message(contact_first_name: str, doctor_label: str, date_str: str, patient_first_name: str | None = None) -> str:
    consulta = f"da consulta de *{patient_first_name}*" if patient_first_name else "da sua consulta"
    return (
        f"Olá, {contact_first_name}! 😊 Como não recebemos a confirmação do novo horário "
        f"{consulta} com *{doctor_label}* (agendada para {date_str}), liberamos a vaga "
        f"para que outros pacientes possam utilizá-la.\n\n"
        f"Quando quiser remarcar, é só nos chamar aqui — ficaremos felizes em encontrar "
        f"um novo horário para você! 🩷"
    )


async def send_whatsapp(phone: str, text: str) -> None:
    from app.whatsapp import send_text
    phone_fmt = f"{phone}@s.whatsapp.net" if "@" not in phone else phone
    await send_text(phone_fmt, text)


async def cancel_calendar_event(appointment_id: str, doctor_id: str, client) -> None:
    try:
        result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
        calendar_id = result.data.get("agenda_id") if result.data else None
        if not calendar_id:
            print(f"  Sem calendar_id para doctor {doctor_id}")
            return
        from app.google_calendar import cancel_event
        await cancel_event(calendar_id, appointment_id)
        print(f"  ✅ Slot liberado no Google Calendar: {appointment_id}")
    except Exception as e:
        print(f"  ⚠️  Falha ao cancelar no Calendar (não fatal): {e}")


async def main():
    from supabase import acreate_client
    from app.database import get_last_assistant_message_time

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ)

    if not (WINDOW_START <= now.hour < WINDOW_END):
        print(f"Fora da janela de envio ({WINDOW_START}h–{WINDOW_END}h). Encerrando.")
        return

    timeout_threshold = (now - timedelta(minutes=TIMEOUT_MINUTES)).isoformat()

    # Busca consultas com reagendamento em andamento há mais de 1h
    result = await client.from_("appointments").select(
        "appointment_id, start_time, doctor_id, reschedule_requested_at, "
        "users(number, name, patient_name)"
    ).eq("status", "scheduled") \
     .not_.is_("reschedule_requested_at", "null") \
     .lte("reschedule_requested_at", timeout_threshold) \
     .gte("start_time", now.isoformat()) \
     .execute()

    appts = result.data or []
    print(f"Consultas com reagendamento pendente (>{TIMEOUT_MINUTES}min): {len(appts)}")

    for appt in appts:
        appointment_id = appt["appointment_id"]
        user = appt.get("users") or {}
        phone = user.get("number", "")
        if not phone:
            continue

        # Verifica se o paciente respondeu na última hora
        last_msg_time = await get_last_assistant_message_time(phone)
        # Reutilizamos get_last_assistant_message_time; para checar mensagem do paciente,
        # buscamos diretamente
        from app.database import get_supabase as _get_sb, _strip_phone
        _sb = await _get_sb()
        last_user_msg = await _sb.from_("messages").select("created_at") \
            .eq("phone", _strip_phone(phone)).eq("role", "user") \
            .order("created_at", desc=True).limit(1).execute()

        if last_user_msg.data:
            last_user_dt = datetime.fromisoformat(last_user_msg.data[0]["created_at"]).astimezone(TZ)
            if (now - last_user_dt) < timedelta(minutes=TIMEOUT_MINUTES):
                print(f"  ⏭️  Paciente respondeu há {now - last_user_dt} — aguardando confirmação")
                continue

        start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
        date_str = start_dt.strftime("%d/%m/%Y às %H:%M")
        doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")
        doctor_id = appt.get("doctor_id", "")

        contact_name = user.get("name") or user.get("patient_name") or "paciente"
        patient_name = user.get("patient_name") or ""
        from app.utils import display_name as _dn
        contact_first = _dn(contact_name)
        patient_first = _dn(patient_name) if patient_name and patient_name != contact_name else None

        # 1. Notifica paciente (obrigatório para liberar)
        msg = pending_reschedule_message(contact_first, doctor_label, date_str, patient_first)
        whatsapp_ok = False
        try:
            await send_whatsapp(phone, msg)
            whatsapp_ok = True
            print(f"  ✅ Notificado: {patient_name or contact_name} ({phone})")
        except Exception as e:
            print(f"  ⚠️  WhatsApp falhou para {phone} — slot não liberado: {e}")

        if not whatsapp_ok:
            continue

        # 2. Libera slot no Google Calendar
        await cancel_calendar_event(appointment_id, doctor_id, client)

        # 3. Muda status para pending_reschedule
        await client.from_("appointments").update({
            "status": "pending_reschedule",
            "updated_at": now.isoformat(),
        }).eq("appointment_id", appointment_id).execute()
        print(f"  ✅ Status → pending_reschedule: {appointment_id}")


if __name__ == "__main__":
    asyncio.run(main())
