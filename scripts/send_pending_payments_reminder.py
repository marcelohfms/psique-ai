"""
Daily email reminder to the attendant listing patients with pending payments.
Runs every morning via GitHub Actions.

Two sections:
  1. Taxa de reserva pendente  — scheduled appointments where booking_fee_paid_at IS NULL
  2. Pagamento de consulta pendente — completed appointments where paid_at IS NULL

The attendant can then open each patient's conversation in Chatwoot and register
the payment via private note: "PAGAMENTO PRESENCIAL [nome] R$ [valor]"
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}

BOOKING_FEE = 100


def _fmt_dt(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y às %H:%M")


def _fmt_date(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(TZ).strftime("%d/%m/%Y")


async def main() -> None:
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    now = datetime.now(TZ)

    # ── 1. Taxa de reserva pendente (agendadas, futuras, sem booking_fee_paid_at) ──
    r1 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name)")
        .eq("status", "scheduled")
        .eq("booking_fee_waived", False)
        .is_("booking_fee_paid_at", "null")
        .gt("start_time", now.isoformat())
        .order("start_time")
        .execute()
    )
    taxa_pendente = r1.data or []

    # ── 2. Pagamento de consulta pendente (realizadas, sem paid_at) ──────────────
    r2 = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, consultation_type, booking_fee_paid_at, paid_at, users(number, patient_name, name, custom_price)")
        .eq("status", "completed")
        .is_("paid_at", "null")
        .order("start_time", desc=True)
        .execute()
    )
    # Exclude courtesy patients (custom_price == 0) — they have no balance to collect
    consulta_pendente = [
        appt for appt in (r2.data or [])
        if (appt.get("users") or {}).get("custom_price") != 0
    ]

    # ── Build email ───────────────────────────────────────────────────────────────
    total = len(taxa_pendente) + len(consulta_pendente)
    if total == 0:
        print("Nenhum pagamento pendente encontrado — e-mail não enviado.")
        return

    today_str = now.strftime("%d/%m/%Y")
    taxa_em_aberto = len(taxa_pendente) * BOOKING_FEE

    lines = [
        f"Resumo de pagamentos pendentes — {today_str}",
        "=" * 50,
        f"Taxa de reserva em aberto: {len(taxa_pendente)}x R${BOOKING_FEE} = R${taxa_em_aberto}",
        f"Consultas pendentes de pagamento: {len(consulta_pendente)}",
        "",
    ]

    if taxa_pendente:
        lines.append(f"TAXA DE RESERVA PENDENTE ({len(taxa_pendente)} consulta(s) agendada(s)):")
        lines.append("-" * 40)
        for appt in taxa_pendente:
            user = appt.get("users") or {}
            patient = user.get("patient_name") or user.get("name") or "—"
            contact = user.get("name") or "—"
            phone = user.get("number") or "—"
            doctor = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "—")
            dt = _fmt_dt(appt["start_time"])
            ctype = appt.get("consultation_type") or ""

            line = f"• {patient}"
            if contact and contact != patient:
                line += f"\n  Responsável: {contact}"
            line += f"\n  {doctor} — {dt}"
            if ctype:
                line += f" [{ctype}]"
            line += f"\n  WhatsApp: {phone}"
            line += f"\n  Taxa de reserva: Pendente — R${BOOKING_FEE},00 em aberto"
            lines.append(line)
            lines.append("")

    if consulta_pendente:
        lines.append(f"PAGAMENTO DE CONSULTA PENDENTE ({len(consulta_pendente)} consulta(s) realizada(s)):")
        lines.append("-" * 40)
        for appt in consulta_pendente:
            user = appt.get("users") or {}
            patient = user.get("patient_name") or user.get("name") or "—"
            contact = user.get("name") or "—"
            phone = user.get("number") or "—"
            doctor = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "—")
            dt = _fmt_date(appt["start_time"])
            taxa_paga = bool(appt.get("booking_fee_paid_at"))

            line = f"• {patient}"
            if contact and contact != patient:
                line += f"\n  Responsável: {contact}"
            line += f"\n  {doctor} — realizada em {dt}"
            line += f"\n  WhatsApp: {phone}"
            if taxa_paga:
                line += f"\n  Taxa de reserva: Paga (R${BOOKING_FEE},00) — consulta pendente"
            else:
                line += f"\n  Taxa de reserva: Não paga — R${BOOKING_FEE},00 + consulta em aberto"
            lines.append(line)
            lines.append("")

    lines += [
        "─" * 50,
        "Para registrar um pagamento presencial, abra a conversa do paciente no",
        "Chatwoot e envie uma nota privada com o formato:",
        "  PAGAMENTO PRESENCIAL [nome do paciente] R$ [valor]",
    ]

    body = "\n".join(lines)
    subject = f"Psique — Pagamentos pendentes ({total}) — {today_str}"

    print(body)
    print()

    # Guard: fail loudly if credentials are missing so CI reflects the real state
    missing = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "CLINIC_NOTIFY_EMAIL") if not os.environ.get(v)]
    if missing:
        raise EnvironmentError(f"Variáveis de ambiente ausentes: {', '.join(missing)} — e-mail NÃO enviado.")

    from app.email_sender import send_clinic_notification_email
    await send_clinic_notification_email(subject, body)
    print(f"E-mail enviado: {total} pagamento(s) pendente(s).")


if __name__ == "__main__":
    asyncio.run(main())
