"""
Levantamento (somente leitura) dos pacientes travados em remarcação.

Um paciente fica "travado" quando iniciou uma remarcação — o slot antigo já foi
liberado do Google Calendar e a consulta virou status='pending_reschedule'
(ver mark_reschedule_in_progress em app/graph/tools.py e o cron
release_pending_reschedules.py) — mas nunca confirmou o novo horário. Quando
confirma, reschedule_appointment volta o status para 'scheduled'.

Este script NÃO envia nada e NÃO altera o banco. Só lista quem está nesse estado,
para dimensionar quantos pacientes entrariam no resgate (template resgate_remarcacao,
ver docs/whatsapp-templates.md) antes de automatizar o envio.

Uso:
    uv run python scripts/_list_pending_reschedules.py
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient
from app.utils import display_name

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}


def _humanize_delta(delta) -> str:
    total_min = int(delta.total_seconds() // 60)
    if total_min < 60:
        return f"{total_min}min"
    hours = total_min // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


async def main():
    from supabase import acreate_client

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ)

    result = await (
        client.from_("appointments")
        .select(
            "appointment_id, start_time, doctor_id, modality, patient_id, status, "
            "reschedule_requested_at, reschedule_initiated_by, "
            "booking_fee_paid_at, booking_fee_waived, patients(name)"
        )
        .eq("status", "pending_reschedule")
        .order("reschedule_requested_at", desc=False)
        .execute()
    )
    appts = result.data or []

    print(f"📋 Pacientes travados em remarcação (status=pending_reschedule): {len(appts)}")
    print(f"   (referência: {now.strftime('%d/%m/%Y %H:%M')} — America/Recife)")
    print("=" * 78)

    if not appts:
        print("\nNenhum paciente travado. 🎉")
        return

    rescuable = 0
    no_phone = 0
    clinic_initiated = 0

    for appt in appts:
        patient = appt.get("patients") or {}
        patient_name = patient.get("name") or "paciente"
        first_name = display_name(patient_name)
        doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")

        # start_time = o horário ANTIGO (só muda quando reschedule_appointment confirma).
        old_start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
        old_start_str = old_start.strftime("%a, %d/%m às %H:%M")
        old_in_future = old_start > now

        req_at_raw = appt.get("reschedule_requested_at")
        if req_at_raw:
            req_at = datetime.fromisoformat(req_at_raw).astimezone(TZ)
            stuck_for = _humanize_delta(now - req_at)
        else:
            stuck_for = "?"

        initiated_by = appt.get("reschedule_initiated_by") or "patient(legado)"
        fee_paid = bool(appt.get("booking_fee_paid_at") or appt.get("booking_fee_waived"))

        # Telefones dos contatos de consulta (pais/responsáveis incluídos).
        patient_id = appt.get("patient_id")
        contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
        phones = [c.get("phone") for c in contacts if c.get("phone")]

        is_clinic = initiated_by == "clinic"
        if is_clinic:
            clinic_initiated += 1
        if not phones:
            no_phone += 1
        if phones and not is_clinic:
            rescuable += 1

        flags = []
        if is_clinic:
            flags.append("⚠️ iniciada pela CLÍNICA (não conta remarcação do paciente)")
        if not old_in_future:
            flags.append("⏳ horário anterior JÁ PASSOU (não dá pra 'voltar' pra ele)")
        if not phones:
            flags.append("📵 sem telefone de contato")
        if not fee_paid:
            flags.append("💸 taxa de reserva não consta paga")

        print(f"\n• {patient_name}  →  {doctor_label}")
        print(f"    horário anterior: {old_start_str}  ({'futuro' if old_in_future else 'passado'})")
        print(f"    travado há: {stuck_for}   |   iniciada por: {initiated_by}")
        print(f"    telefones: {', '.join(phones) if phones else '—'}")
        print(f"    appointment_id: {appt['appointment_id']}")
        if flags:
            for f in flags:
                print(f"    {f}")

    print("\n" + "=" * 78)
    print(f"Total travados:            {len(appts)}")
    print(f"Resgatáveis (tel + paciente): {rescuable}")
    print(f"Iniciadas pela clínica:    {clinic_initiated}")
    print(f"Sem telefone:              {no_phone}")


if __name__ == "__main__":
    asyncio.run(main())
