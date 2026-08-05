"""Reativa o lembrete do DIA da consulta (06/08 09:00) do Marcelo Filho.

No restore (_restore_marcelo_filho_0608.py) os três carimbos de lembrete foram
gravados para silenciar o cron. A clínica quer o lembrete do dia da consulta:
  - reminder_day_of_sent_at → NULL: send_appointment_reminders envia amanhã. Como a
    consulta é às 09:00 (antes das 10h), a régua manda a partir das 05:00.
  - payment_reminder_sent_at → NULL: nenhuma cobrança chegou a ser enviada, e o carimbo
    não protege nada (send_payment_reminders só olha linhas com booking_fee_paid_at nulo).
  - reminder_day_before_sent_at fica como está: a janela do lembrete de véspera é
    07h–12h do dia anterior (hoje) e já passou; a query só busca consultas do dia
    seguinte, então essa linha nunca mais entra nela.

Uso: uv run python scripts/_enable_marcelo_day_of_reminder.py --apply
"""
import asyncio
import sys
from dotenv import load_dotenv
load_dotenv()

APPLY = "--apply" in sys.argv
EVENT_ID = "m5ugluaibe7ub4c22n01mcbugo"  # 06/08/2026 09:00 — 1ª hora, responsáveis


async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    row = (await client.from_("appointments").select("*")
           .eq("appointment_id", EVENT_ID).single().execute()).data
    st = datetime.fromisoformat(row["start_time"]).astimezone(TZ)
    print(f"{st:%d/%m/%Y %H:%M} | status={row['status']} | taxa={row['booking_fee_paid_at']}")
    print(f"  antes: day_of={row['reminder_day_of_sent_at']} payment={row['payment_reminder_sent_at']}")

    if row["status"] != "scheduled" or not row["booking_fee_paid_at"]:
        print("❌ Linha não está scheduled/paga — abortando.")
        return

    if not APPLY:
        print("\n[DRY-RUN] Rode com --apply.")
        return

    await client.from_("appointments").update({
        "reminder_day_of_sent_at": None,
        "payment_reminder_sent_at": None,
    }).eq("appointment_id", EVENT_ID).execute()

    after = (await client.from_("appointments").select("*")
             .eq("appointment_id", EVENT_ID).single().execute()).data
    print(f"  depois: day_of={after['reminder_day_of_sent_at']} payment={after['payment_reminder_sent_at']}")
    print("✅ Lembrete do dia da consulta reativado para 06/08 (sai a partir das 05:00).")


asyncio.run(main())
