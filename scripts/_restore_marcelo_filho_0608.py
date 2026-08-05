"""Restaura a 1ª parte da primeira consulta do Marcelo Filho (06/08/2026 09:00).

Contexto: a 1ª consulta de menor com o Dr. Júlio foi dividida em duas sessões de 1h
em dias diferentes (06/08 09:00 com os responsáveis, 13/08 14:00 com o paciente).
Ao confirmar a 2ª sessão, o Guard 0 do confirm_appointment enxergou "paciente já tem
consulta futura" e a Eva remarcou a 1ª em vez de criar a 2ª: o evento de 06/08 foi
cancelado no Calendar e a MESMA linha de appointments foi movida para 13/08.

Este script recria a sessão de 06/08 (evento + linha), sem tocar na de 13/08 e
SEM ENVIAR NADA AO PACIENTE:
  - booking_fee_paid_at copiado da linha de 13/08 (é a mesma taxa de R$ 100 da
    primeira consulta) — sem isso, send_payment_reminders cobraria e auto-cancelaria.
  - reminder_day_before / reminder_day_of / payment_reminder marcados como já
    enviados, para que o cron de lembretes não dispare mensagem nenhuma.

Uso: uv run python scripts/_restore_marcelo_filho_0608.py --apply
"""
import asyncio
import sys
from dotenv import load_dotenv
load_dotenv()

APPLY = "--apply" in sys.argv

PATIENT_ID = "d640f9e3-95c3-4790-b381-b930186e8f8c"      # Marcelo Rodrigues de Souza Brayner Filho
CONTACT_ID = "21499bff-17b3-4f8c-acc2-fd76835e2c1a"      # 5581999865181 (pai)
DOCTOR_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"       # Dr. Júlio
AGENDA = "dr.juliogouveia@gmail.com"
EXISTING_APPT = "dtkqjgk94201rec9nd9p9jnvng"             # 2ª sessão, 13/08 14:00
SESSION_NOTE = "1ª hora — responsáveis"


async def main():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.database import get_supabase
    from app.google_calendar import create_event

    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    start = datetime(2026, 8, 6, 9, 0, tzinfo=TZ)
    end = start + timedelta(minutes=60)

    # ── Pré-checagens ────────────────────────────────────────────────────────
    patient = (await client.from_("patients").select("*").eq("id", PATIENT_ID).single().execute()).data
    print(f"Paciente: {patient['name']} ({patient['age']} anos) — {patient['email']}")

    existing = (await client.from_("appointments").select("*")
                .eq("patient_id", PATIENT_ID).order("start_time").execute()).data
    print(f"Agendamentos hoje no banco: {len(existing)}")
    for a in existing:
        st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"  {st} | {a['status']} | {a['appointment_id']}")

    if any(a["start_time"] == start.isoformat() and a["status"] == "scheduled" for a in existing):
        print("\n⚠️  Já existe linha scheduled para 06/08 09:00 — nada a fazer.")
        return

    ref = next((a for a in existing if a["appointment_id"] == EXISTING_APPT), None)
    if not ref:
        print("\n❌ Linha de referência (13/08) não encontrada. Abortando.")
        return
    fee_paid_at = ref.get("booking_fee_paid_at")
    print(f"\nTaxa de reserva (herdada da linha de 13/08): {fee_paid_at}")
    if not fee_paid_at and not ref.get("booking_fee_waived"):
        print("❌ Linha de referência sem taxa paga — abortando para não gerar cobrança.")
        return

    if not APPLY:
        print("\n[DRY-RUN] Rode com --apply para criar evento + linha.")
        return

    # ── Evento no Calendar ───────────────────────────────────────────────────
    event_id = await create_event(
        calendar_id=AGENDA,
        start=start,
        slot_minutes=60,
        patient_name=patient["name"],
        doctor_name="Dr. Júlio",
        session_note=SESSION_NOTE,
        modality="presencial",
        patient_email=patient.get("email") or "",
        patient_number="5581999865181",
    )
    print(f"\n✅ Evento criado: {event_id}")

    # ── Linha em appointments ────────────────────────────────────────────────
    now_iso = datetime.now(TZ).isoformat()
    try:
        await client.from_("appointments").insert({
            "patient_id": PATIENT_ID,
            "contact_id": CONTACT_ID,
            "doctor_id": DOCTOR_ID,
            "appointment_id": event_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "status": "scheduled",
            "modality": "presencial",
            "consultation_type": "primeira_consulta",
            "booking_fee_waived": bool(ref.get("booking_fee_waived")),
            "booking_fee_paid_at": fee_paid_at,
            # Silencia o cron de lembretes/cobrança: o paciente já foi informado
            # pela Eva e não pode receber nada por causa desta correção.
            "payment_reminder_sent_at": now_iso,
            "reminder_day_before_sent_at": now_iso,
            "reminder_day_of_sent_at": now_iso,
        }).execute()
    except Exception as e:
        from app.google_calendar import cancel_event
        print(f"❌ Insert falhou ({e}) — removendo evento criado.")
        await cancel_event(AGENDA, event_id)
        raise
    print("✅ Linha de appointments criada (06/08 09:00, taxa paga, lembretes silenciados).")

    # ── Marca a 2ª sessão no Calendar para o médico não confundir ────────────
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    service = build("calendar", "v3", credentials=_credentials())
    ev = service.events().get(calendarId=AGENDA, eventId=EXISTING_APPT).execute()
    if "2ª hora" not in (ev.get("summary") or ""):
        ev["summary"] = f"{ev['summary']} (2ª hora — paciente)"
        ev["description"] = (ev.get("description") or "") + "\n\n2ª hora — consulta com o paciente"
        service.events().update(calendarId=AGENDA, eventId=EXISTING_APPT, body=ev).execute()
        print("✅ Evento de 13/08 marcado como '2ª hora — paciente'.")

    final = (await client.from_("appointments").select("*")
             .eq("patient_id", PATIENT_ID).order("start_time").execute()).data
    print("\n=== ESTADO FINAL ===")
    for a in final:
        st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"  {st} | {a['status']} | taxa={a['booking_fee_paid_at']} | {a['appointment_id']}")


asyncio.run(main())
