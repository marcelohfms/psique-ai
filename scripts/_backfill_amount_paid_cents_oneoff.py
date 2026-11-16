"""One-off: preenche appointments.amount_paid_cents dos registros antigos.

A coluna nasceu com DEFAULT 0. Consultas pagas pelo código antigo ficaram com 0,
e sem este preenchimento quem já pagou a taxa e depois paga o saldo nunca
chegaria ao valor esperado.

Regras (valores absolutos, nunca incrementos):
  - paid_at preenchido: amount_paid_cents = valor cheio esperado (mesma regra de
    preço do register_payment) em centavos.
  - booking_fee_paid_at preenchido e paid_at vazio: 10000 (R$100, conservador).
  - demais linhas: ficam em 0.

Idempotente: só toca linhas com amount_paid_cents = 0 e só grava se o alvo for
maior que 0. Rodar duas vezes não muda nada na segunda.

Uso:
  uv run python -m scripts._backfill_amount_paid_cents_oneoff --dry-run
  uv run python -m scripts._backfill_amount_paid_cents_oneoff
"""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
BOOKING_FEE_CENTS = 10000
PAGE = 1000


def _target_cents(appt: dict, patient: dict | None) -> tuple[int, str]:
    """Devolve (centavos, motivo) para uma consulta. 0 significa não mexer."""
    from app.database import DOCTOR_NAMES
    from app.dates import compute_age
    from app.graph.tools import _expected_consultation_amount

    if appt.get("paid_at"):
        start = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
        age = compute_age((patient or {}).get("birth_date"), start.date())
        if age is None:
            age = 99  # mesmo fallback do register_payment
        doctor_key = DOCTOR_NAMES.get(appt.get("doctor_id") or "", "")
        custom_price = (patient or {}).get("custom_price")
        if appt.get("is_courtesy"):
            custom_price = 0
        expected = _expected_consultation_amount(
            doctor_key,
            age,
            appt.get("consultation_type"),
            start,
            price_override=custom_price,
        )
        return int(expected) * 100, f"paga (esperado R${expected})"
    if appt.get("booking_fee_paid_at"):
        return BOOKING_FEE_CENTS, "so taxa de reserva"
    return 0, "sem pagamento"


async def main(dry_run: bool) -> None:
    from app.database import get_supabase

    client = await get_supabase()
    changed = skipped = 0
    # Primeiro lê todas as candidatas (sem gravar), para a paginação não andar
    # junto com as atualizações.
    candidates: list[dict] = []
    offset = 0
    while True:
        rows = (
            await client.from_("appointments")
            .select(
                "appointment_id,patient_id,doctor_id,start_time,consultation_type,"
                "is_courtesy,paid_at,booking_fee_paid_at,amount_paid_cents"
            )
            .eq("amount_paid_cents", 0)
            .or_("paid_at.not.is.null,booking_fee_paid_at.not.is.null")
            .order("appointment_id")
            .range(offset, offset + PAGE - 1)
            .execute()
        ).data or []
        candidates.extend(rows)
        if len(rows) < PAGE:
            break
        offset += PAGE
    pids = list({r["patient_id"] for r in candidates if r.get("patient_id")})
    patients: dict[str, dict] = {}
    for i in range(0, len(pids), 200):
        pres = (
            await client.from_("patients")
            .select("id,birth_date,custom_price")
            .in_("id", pids[i : i + 200])
            .execute()
        ).data or []
        patients.update({p["id"]: p for p in pres})
    for r in candidates:
        cents, why = _target_cents(r, patients.get(r.get("patient_id")))
        if cents <= 0:
            skipped += 1
            continue
        print(f"{'[dry-run] ' if dry_run else ''}{r['appointment_id']} -> {cents} ({why})")
        if not dry_run:
            await client.from_("appointments").update(
                {"amount_paid_cents": cents}
            ).eq("appointment_id", r["appointment_id"]).eq(
                "amount_paid_cents", 0
            ).execute()
        changed += 1
    print(f"{'Simulado' if dry_run else 'Atualizado'}: {changed} linhas; ignoradas: {skipped}")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
