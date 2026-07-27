"""
One-off: corrige confusão de agendamento de Mariana Melo Gadelha (5581993062020).

Histórico (ver events, phone=5581993062020):
- 21/07 17:34: consulta original criada para 27/07 11:00 (Dr. Júlio), taxa de
  reserva paga em 21/07 17:37 (recibo registrado, drive_link ok).
- 27/07 12:55: nota da atendente pede cancelar a consulta das 11:00.
- 27/07 12:56: atendente cancela a consulta das 11:00 (appointment_id
  pdeb8got07s9d1k5qe35ujjccs -> status canceled, evento removido do Calendar)
  e cria uma nova consulta "encaixe" para 27/07 13:30 (appointment_id
  iih0bknlu3lcme38uimb9ieb0g), atribuindo a ela a MESMA taxa já paga em 21/07
  (nota: "o valor de antecipação já foi pago" — não é uma cobrança nova).
- No fim, a paciente preferiu remarcar para 03/08 às 10:00, não 27/07 13:30.
  O evento das 13:30 nunca deveria ter existido.

Correção (a pedido da atendente/clínica):
1. Apaga de vez o evento indevido de hoje 13:30: remove do Google Calendar E
   deleta a linha em `appointments` (não apenas cancela — deve sumir do
   histórico).
2. Reativa a consulta original (pdeb8got07s9d1k5qe35ujjccs, já com taxa paga):
   muda status de "canceled" para "scheduled" e move a data para 03/08 10:00.
   Como o evento antigo já foi removido do Calendar quando foi cancelado,
   cria um evento NOVO e atualiza appointment_id com o novo event_id (mesmo
   padrão usado por reschedule_appointment para status pending_reschedule/
   canceled — ver app/graph/tools.py).

Confirmado: nenhum agendamento do Dr. Júlio em 03/08 10:00 (slot livre).
Confirmado: nenhuma linha em `payments` referencia qualquer um dos dois
appointment_id (payment_id é None nos dois) — apagar a linha das 13:30 não
perde nenhum registro financeiro distinto (a taxa é a mesma de 21/07).

Uso: uv run python scripts/_fix_mariana_melo_gadelha_reschedule_oneoff.py
"""
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581993062020"
PATIENT_ID = "8f5b05a3-037b-4d10-b2b6-b11c1a6848f1"

WRONG_APPOINTMENT_ID = "iih0bknlu3lcme38uimb9ieb0g"
WRONG_START_TIME = "2026-07-27T16:30:00+00:00"

ORIG_APPOINTMENT_ID = "pdeb8got07s9d1k5qe35ujjccs"
ORIG_START_TIME = "2026-07-27T14:00:00+00:00"

DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
PATIENT_NAME = "Mariana Melo Gadelha"
PATIENT_EMAIL = "Marianacgadelha@gmail.com"
NEW_START = datetime(2026, 8, 3, 10, 0, tzinfo=TZ)
SLOT_MINUTES = 60
MODALITY = "presencial"


async def main():
    from app.database import get_supabase, log_event
    from app.graph.tools import _get_doctor_calendar_id, _notify_clinic
    from app.google_calendar import create_event, cancel_event

    client = await get_supabase()

    # --- Validação da consulta indevida (13:30 hoje) ---
    wrong = (
        await client.from_("appointments")
        .select("*")
        .eq("appointment_id", WRONG_APPOINTMENT_ID)
        .maybe_single()
        .execute()
    )
    if not wrong.data:
        print("⚠️  Agendamento indevido (13:30) não encontrado. Abortando.")
        return
    if wrong.data.get("status") != "scheduled":
        print(f"⚠️  Status inesperado no agendamento indevido ({wrong.data.get('status')}). Abortando.")
        return
    if wrong.data.get("patient_id") != PATIENT_ID:
        print(f"⚠️  patient_id não bate no agendamento indevido ({wrong.data.get('patient_id')}). Abortando.")
        return
    if wrong.data.get("start_time") != WRONG_START_TIME:
        print(f"⚠️  start_time inesperado no agendamento indevido ({wrong.data.get('start_time')}). Abortando.")
        return

    # --- Validação da consulta original (11:00 hoje, cancelada) ---
    orig = (
        await client.from_("appointments")
        .select("*")
        .eq("appointment_id", ORIG_APPOINTMENT_ID)
        .maybe_single()
        .execute()
    )
    if not orig.data:
        print("⚠️  Agendamento original (11:00) não encontrado. Abortando.")
        return
    if orig.data.get("status") != "canceled":
        print(f"⚠️  Status inesperado no agendamento original ({orig.data.get('status')}). Abortando.")
        return
    if orig.data.get("patient_id") != PATIENT_ID:
        print(f"⚠️  patient_id não bate no agendamento original ({orig.data.get('patient_id')}). Abortando.")
        return
    if orig.data.get("start_time") != ORIG_START_TIME:
        print(f"⚠️  start_time inesperado no agendamento original ({orig.data.get('start_time')}). Abortando.")
        return

    calendar_id = await _get_doctor_calendar_id(DOCTOR_KEY)

    # 1. Remove o evento indevido do Google Calendar
    try:
        await cancel_event(calendar_id, WRONG_APPOINTMENT_ID)
        print(f"✅ Evento indevido {WRONG_APPOINTMENT_ID} removido do Google Calendar")
    except Exception as e:
        print(f"⚠️  Falha ao remover evento indevido do Calendar (pode já ter sido removido): {e}")

    # 2. Deleta de vez a linha do agendamento indevido (não apenas cancela)
    await client.from_("appointments").delete().eq("appointment_id", WRONG_APPOINTMENT_ID).execute()
    print(f"✅ Linha do agendamento indevido ({WRONG_APPOINTMENT_ID}) deletada do banco")

    # 3. Cria novo evento no Calendar para a consulta original, na data certa
    new_event_id = await create_event(
        calendar_id=calendar_id,
        start=NEW_START,
        slot_minutes=SLOT_MINUTES,
        patient_name=PATIENT_NAME,
        doctor_name=DOCTOR_LABEL,
        modality=MODALITY,
        patient_email=PATIENT_EMAIL,
        patient_number=PHONE,
    )
    print(f"✅ Novo evento criado no Google Calendar: {new_event_id} em {NEW_START.isoformat()}")

    # 4. Reativa e atualiza a consulta original no banco
    new_end = NEW_START + timedelta(minutes=SLOT_MINUTES)
    await client.from_("appointments").update({
        "appointment_id": new_event_id,
        "status": "scheduled",
        "start_time": NEW_START.isoformat(),
        "end_time": new_end.isoformat(),
        "reminder_day_before_sent_at": None,
        "reminder_day_of_sent_at": None,
        "reschedule_requested_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("appointment_id", ORIG_APPOINTMENT_ID).execute()
    print("✅ Agendamento original reativado e movido para 03/08/2026 às 10:00")

    # 5. Loga o evento (initiated_by=clinic: correção de erro da atendente,
    # não conta como remarcação gratuita da paciente)
    await log_event("appointment_rescheduled", PHONE, {
        "appointment_id": new_event_id,
        "old_appointment_id": ORIG_APPOINTMENT_ID,
        "deleted_wrong_appointment_id": WRONG_APPOINTMENT_ID,
        "new_datetime": NEW_START.isoformat(),
        "initiated_by": "clinic",
    })

    # 6. Notifica clínica
    await _notify_clinic(
        f"Agendamento corrigido! 🔄\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Confusão: consulta original de hoje às 11:00 foi cancelada indevidamente "
        f"e uma consulta de encaixe às 13:30 foi criada, mas a paciente havia "
        f"preferido remarcar para 03/08. O evento das 13:30 foi apagado.\n"
        f"Consulta original (com taxa já paga) foi reativada e movida para "
        f"03/08/2026 às 10:00.\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Modalidade: {MODALITY}\n"
        "Correção feita manualmente via script.",
        phone=PHONE,
        subject=f"Agendamento corrigido — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
