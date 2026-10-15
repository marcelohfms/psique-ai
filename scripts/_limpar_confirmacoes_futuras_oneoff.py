"""One-off: zera confirmed_at de consultas FUTURAS (2+ dias) que ficaram marcadas
como confirmadas antes da trava de véspera, e desfaz a marca no Google Calendar.

Uso:
    uv run python scripts/_limpar_confirmacoes_futuras_oneoff.py           # dry-run (só lista)
    uv run python scripts/_limpar_confirmacoes_futuras_oneoff.py --apply   # aplica

Rodar no servidor da Eva (o DNS do Supabase é recusado no Mac local).
"""
import asyncio
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Recife")


async def main(apply: bool) -> None:
    from app.supabase_client import get_supabase
    from app.google_calendar import mark_event_unconfirmed

    client = await get_supabase()
    res = (
        await client.from_("appointments")
        .select("appointment_id, start_time, confirmed_at, status, doctor_id, patients(name)")
        .eq("status", "scheduled")
        .not_.is_("confirmed_at", "null")
        .order("start_time")
        .execute()
    )
    rows = res.data or []
    today = datetime.now(TZ).date()

    alvos = []
    for r in rows:
        st = r.get("start_time")
        if not st:
            continue
        try:
            appt_date = datetime.fromisoformat(st).astimezone(TZ).date()
        except (ValueError, TypeError):
            continue
        if (appt_date - today).days >= 2:
            alvos.append(r)

    print(f"{len(alvos)} consulta(s) futura(s) confirmada(s) a 2+ dias:")
    for r in alvos:
        nome = (r.get("patients") or {}).get("name", "?")
        print(f"  {r['appointment_id']} | {r['start_time']} | {nome}")

    if not apply:
        print("\nDry-run. Rode com --apply para zerar confirmed_at e desmarcar no Calendar.")
        return

    agendas: dict[str, str] = {}
    for r in alvos:
        appt_id = r["appointment_id"]
        await client.from_("appointments").update(
            {"confirmed_at": None}
        ).eq("appointment_id", appt_id).execute()

        doctor_id = r.get("doctor_id")
        if doctor_id:
            if doctor_id not in agendas:
                d = (
                    await client.from_("doctors")
                    .select("agenda_id")
                    .eq("doctor_id", doctor_id)
                    .single()
                    .execute()
                )
                agendas[doctor_id] = (d.data or {}).get("agenda_id") or ""
            cal = agendas[doctor_id]
            if cal:
                try:
                    await mark_event_unconfirmed(cal, appt_id)
                except Exception as e:  # noqa: BLE001
                    print(f"  ! Calendar falhou em {appt_id}: {e}")
        print(f"  ✓ {appt_id} desconfirmada")

    print(f"\nPronto: {len(alvos)} consulta(s) processada(s).")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
