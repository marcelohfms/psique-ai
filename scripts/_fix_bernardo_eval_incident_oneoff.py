"""
One-off: corrige incidente causado por um subagent de avaliação de skill que,
sem sandbox, executou ações reais contra o Bernardo Rabelo Porto Ferreira
(patient_id c73a61f1-52ef-42fa-b468-44ddb546d878) durante um teste da skill
psique-primeira-consulta.

O subagent "sem skill" criou eventos/linhas duplicadas para os dois momentos
e rodou uma limpeza que apagou por ordenação de ID (não por correção),
resultando em:
  1. Linha sobrevivente do momento 1 (23/07 19h-20h, DB id 41993210-...)
     sem booking_fee_paid_at — a taxa foi paga de fato em 2026-07-09, só o
     registro se perdeu. Mostra "em aberto" no dashboard indevidamente.
  2. Evento fantasma no Google Calendar real do Dr. Júlio
     (lef0g981j9qmrignnnmr4e5j8g, 30/07 15h-16h) sem linha correspondente
     no banco — duplicata órfã do momento 2.

Este script:
  1. Restaura booking_fee_paid_at na linha 41993210 (mesmo timestamp usado
     no registro original, 2026-07-09T14:05:21.698073+00:00).
  2. Cancela o evento fantasma no Calendar.
"""
import asyncio
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

ROW1_ID = "41993210-49f8-4621-a1a1-e5b632a1a47e"
BOOKING_FEE_PAID_AT = "2026-07-09T14:05:21.698073+00:00"
GHOST_EVENT_ID = "lef0g981j9qmrignnnmr4e5j8g"
PATIENT_ID = "c73a61f1-52ef-42fa-b468-44ddb546d878"


async def main():
    from app.database import get_supabase
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event

    client = await get_supabase()

    # 1. Restore booking_fee_paid_at on surviving moment-1 row
    now_iso = datetime.now(timezone.utc).isoformat()
    await client.from_("appointments").update({
        "booking_fee_paid_at": BOOKING_FEE_PAID_AT,
        "updated_at": now_iso,
    }).eq("id", ROW1_ID).execute()
    print(f"[FIX 1] booking_fee_paid_at restaurado na linha {ROW1_ID}.")

    # 2. Cancel ghost calendar event (no DB row backing it)
    calendar_id = await _get_doctor_calendar_id("julio")
    try:
        await cancel_event(calendar_id, GHOST_EVENT_ID)
        print(f"[FIX 2] Evento fantasma {GHOST_EVENT_ID} cancelado no Calendar.")
    except Exception as e:
        print(f"[FIX 2] ERRO ao cancelar evento fantasma: {e}")

    # ── Verification ────────────────────────────────────────────────────────
    appts = await client.from_("appointments").select(
        "id, appointment_id, start_time, end_time, status, consultation_type, booking_fee_paid_at"
    ).eq("patient_id", PATIENT_ID).order("start_time").execute()
    print("\n=== Estado final (appointments) ===")
    for a in appts.data:
        print(" ", a)


asyncio.run(main())
