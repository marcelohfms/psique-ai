"""Confere o estado dos lembretes das duas sessões do Marcelo Filho."""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PID = "d640f9e3-95c3-4790-b381-b930186e8f8c"


async def main():
    from app.database import get_supabase
    from app.patients import get_contacts_for_patient
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    print(f"agora: {datetime.now(TZ):%d/%m/%Y %H:%M} (Recife)\n")

    a = await client.from_("appointments").select("*").eq("patient_id", PID).order("start_time").execute()
    for x in a.data:
        st = datetime.fromisoformat(x["start_time"]).astimezone(TZ)
        print(f"{st:%d/%m/%Y %H:%M} | status={x['status']} | {x['appointment_id']}")
        print(f"   created_at              = {x.get('created_at')}")
        print(f"   reminder_day_before_sent_at = {x.get('reminder_day_before_sent_at')}")
        print(f"   reminder_day_of_sent_at     = {x.get('reminder_day_of_sent_at')}")
        print(f"   payment_reminder_sent_at    = {x.get('payment_reminder_sent_at')}")
        print()

    print("=== CONTATOS com role 'consulta' (destinatários do lembrete) ===")
    contacts = await get_contacts_for_patient(PID, "consulta", include_inactive=True)
    for c in contacts:
        print(" ", c)
    if not contacts:
        print("  ⚠️  NENHUM — o lembrete daria [SKIP] sem enviar nada.")


asyncio.run(main())
