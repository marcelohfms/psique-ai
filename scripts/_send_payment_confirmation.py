"""
Script genérico para enviar confirmação de pagamento com inteligência:
- Verifica o status da consulta (scheduled vs completed)
- Verifica se a data/hora já passou
- Adapta a mensagem conforme o estado (futura, já ocorrida, etc)

Uso: uv run python scripts/_send_payment_confirmation.py <patient_id> <phone> [amount]

Exemplo:
  uv run python scripts/_send_payment_confirmation.py \
    fc926333-5d66-4293-9223-2a7cc64a26d9 \
    5581987516312 \
    650
"""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")


async def main():
    if len(sys.argv) < 3:
        print("❌ Uso: python _send_payment_confirmation.py <patient_id> <phone> [amount]")
        sys.exit(1)

    patient_id = sys.argv[1]
    phone = sys.argv[2]
    amount = sys.argv[3] if len(sys.argv) > 3 else ""

    from app.database import get_supabase
    from app.whatsapp import send_text

    client = await get_supabase()

    # Busca dados do paciente e agendamento mais recente
    appt = await client.from_("appointments").select(
        "appointment_id, start_time, status, patients(name)"
    ).eq("patient_id", patient_id).order("start_time", desc=True).limit(1).execute()

    if not appt.data:
        print("❌ Agendamento não encontrado para este paciente")
        return

    appt_data = appt.data[0]
    patient_name = (appt_data.get("patients") or {}).get("name", "Paciente")
    start_time_iso = appt_data["start_time"]
    status = appt_data["status"]

    # Converte para horário de Recife
    start_dt = datetime.fromisoformat(start_time_iso).astimezone(TZ)
    now = datetime.now(TZ)
    has_occurred = start_dt <= now or status == "completed"

    # Formata data/hora para exibição
    date_str = start_dt.strftime("%d/%m")
    time_str = start_dt.strftime("%H:%M")

    # Busca informações do médico
    doctor_id = appt_data.get("doctor_id", "")
    doctor_labels = {
        "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
        "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
    }
    doctor_label = doctor_labels.get(doctor_id, "médico(a)")

    # Determina se é hoje ou outro dia
    today = datetime.now(TZ).date()
    is_today = start_dt.date() == today

    # Constrói a mensagem conforme o estado
    first_name = patient_name.split()[0]

    if has_occurred:
        # Consulta já ocorreu — reconhece só o pagamento
        date_ref = "hoje" if is_today else f"em {date_str}"
        message = (
            f"Olá, {first_name}! 👋\n\n"
            f"Recebemos o seu pagamento"
            + (f" de R$ {amount}" if amount else "")
            + f" referente à consulta com {doctor_label} {date_ref}, {time_str}. ✅\n\n"
            f"Obrigado! Qualquer dúvida, estou à disposição!"
        )
    else:
        # Consulta ainda não ocorreu — reconhece pagamento e confirma consulta
        message = (
            f"Olá, {first_name}! 👋\n\n"
            f"Recebemos o seu pagamento"
            + (f" de R$ {amount}" if amount else "")
            + f" referente à consulta com {doctor_label} "
            + ("hoje" if is_today else f"em {date_str}")
            + f", {time_str}.\n\n"
            f"Sua consulta está confirmada! ✅\n\n"
            f"Qualquer dúvida, estou à disposição!"
        )

    print(f"📱 Enviando confirmação para {patient_name}...")
    print(f"   Status da consulta: {status}")
    print(f"   Data/hora: {date_str} às {time_str}")
    print(f"   Já ocorreu? {has_occurred}\n")
    print(f"Mensagem:\n{message}\n")

    await send_text(phone, message)
    print("✅ Mensagem enviada com sucesso!")


if __name__ == "__main__":
    asyncio.run(main())
