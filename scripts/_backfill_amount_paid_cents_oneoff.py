"""One-off: preenche appointments.amount_paid dos registros antigos.

A coluna nasceu com DEFAULT 0. Consultas pagas pelo código antigo ficaram com 0,
e sem este preenchimento quem já pagou a taxa e depois paga o saldo nunca
chegaria ao valor esperado.

Regras (valores absolutos, nunca incrementos):
  - paid_at preenchido: amount_paid = valor cheio esperado (mesma regra de
    preço do register_payment) em reais.
  - booking_fee_paid_at preenchido e paid_at vazio: 100 (R$100, conservador).
  - demais linhas: ficam em 0.

Linhas "so taxa" que tem indicio de parcial ja pago (evento Pagamento Parcial ou
valor diferente de R$100 sem ser taxa) recebem 100 mesmo assim, mas entram numa
lista de AVISO no fim para conferencia manual: o acumulado real pode ser maior.

Idempotente: só toca linhas com amount_paid = 0 e só grava se o alvo for
maior que 0. Rodar duas vezes não muda nada na segunda.

Uso:
  uv run python -m scripts._backfill_amount_paid_oneoff --dry-run
  uv run python -m scripts._backfill_amount_paid_oneoff
"""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
BOOKING_FEE_REAIS = 100
PAGE = 1000


def _target_reais(appt: dict, patient: dict | None) -> tuple[int, str]:
    """Devolve (reais, motivo) para uma consulta. 0 significa não mexer."""
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
        return int(expected), f"paga (esperado R${expected})"
    if appt.get("booking_fee_paid_at"):
        return BOOKING_FEE_REAIS, "so taxa de reserva"
    return 0, "sem pagamento"


def _has_partial_evidence(events: list[dict]) -> bool:
    """True se algum evento de comprovante indica pagamento alem da taxa."""
    for ev in events:
        meta = ev.get("metadata") or {}
        ptype = (meta.get("payment_type") or "").strip()
        if ptype == "Pagamento Parcial":
            return True
        if ptype == "Consulta":
            return True
        try:
            amt = float(str(meta.get("amount", "")).replace(".", "").replace(",", "."))
        except ValueError:
            continue
        if amt > 0 and abs(amt - 100) >= 1 and ptype != "Taxa de Reserva":
            return True
    return False


async def _partial_evidence_by_phone(client, phones: list[str]) -> set[str]:
    """Telefones com evidencia de parcial nos eventos de comprovante."""
    found: set[str] = set()
    for i in range(0, len(phones), 100):
        rows = (
            await client.from_("events")
            .select("phone,metadata")
            .in_("event_type", ["payment_receipt_registered", "payment_registered"])
            .in_("phone", phones[i : i + 100])
            .execute()
        ).data or []
        by_phone: dict[str, list[dict]] = {}
        for ev in rows:
            by_phone.setdefault(ev.get("phone") or "", []).append(ev)
        found.update(ph for ph, evs in by_phone.items() if _has_partial_evidence(evs))
    return found


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
                "contact_id,is_courtesy,paid_at,booking_fee_paid_at,amount_paid"
            )
            .eq("amount_paid", 0)
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
    fee_only = [
        r for r in candidates
        if r.get("booking_fee_paid_at") and not r.get("paid_at")
    ]
    # Telefones dos contatos que agendaram (para casar com events.phone).
    cids = list({r["contact_id"] for r in fee_only if r.get("contact_id")})
    cphone: dict[str, str] = {}
    for i in range(0, len(cids), 200):
        cres = (
            await client.from_("contacts").select("id,phone").in_("id", cids[i : i + 200]).execute()
        ).data or []
        cphone.update({c["id"]: c["phone"] for c in cres if c.get("phone")})
    suspect_phones = await _partial_evidence_by_phone(
        client, sorted(set(cphone.values()))
    ) if cphone else set()
    warnings: list[str] = []
    for r in candidates:
        reais, why = _target_reais(r, patients.get(r.get("patient_id")))
        if reais <= 0:
            skipped += 1
            continue
        print(f"{'[dry-run] ' if dry_run else ''}{r['appointment_id']} -> {reais} ({why})")
        if (
            r.get("booking_fee_paid_at") and not r.get("paid_at")
            and cphone.get(r.get("contact_id")) in suspect_phones
        ):
            warnings.append(r["appointment_id"])
        if not dry_run:
            await client.from_("appointments").update(
                {"amount_paid": reais}
            ).eq("appointment_id", r["appointment_id"]).eq(
                "amount_paid", 0
            ).execute()
        changed += 1
    print(f"{'Simulado' if dry_run else 'Atualizado'}: {changed} linhas; ignoradas: {skipped}")
    if warnings:
        print(
            f"\nAVISO: {len(warnings)} consulta(s) so-taxa com indicio de PARCIAL ja pago. "
            "Receberam 100, mas o acumulado real pode ser maior. Reconcilie a mao:"
        )
        for aid in warnings:
            print(f"  - {aid}")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
