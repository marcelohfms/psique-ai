import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
OLD_ROW = "017c1374-fc1a-4597-b681-74271166b88c"  # 02/07, pending_reschedule (superseded)
NEW_ROW = "cc35bedc-cd99-4d3c-8f01-b5786b48c89f"  # 27/07, scheduled (active)

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    old = await client.from_("appointments").select("status, start_time").eq("id", OLD_ROW).maybe_single().execute()
    if not old.data:
        print("Linha antiga não encontrada. Abortando.")
        return
    if old.data["status"] != "pending_reschedule":
        print(f"Status atual da linha antiga é {old.data['status']!r} (esperado pending_reschedule). Abortando por segurança.")
        return

    # Confirma que o novo agendamento ativo existe e está scheduled antes de fechar o antigo
    new = await client.from_("appointments").select("status, start_time").eq("id", NEW_ROW).maybe_single().execute()
    if not new.data or new.data["status"] != "scheduled":
        print(f"Novo agendamento não está scheduled ({new.data}). Abortando.")
        return

    await client.from_("appointments").update({
        "status": "canceled",
        "reschedule_requested_at": None,
        "updated_at": datetime.now(TZ).isoformat(),
    }).eq("id", OLD_ROW).execute()
    print(f"Linha antiga {OLD_ROW} → canceled (substituída pelo reagendamento de 27/07).")

    check = await client.from_("appointments").select("id, status, start_time").eq("patient_id", "b7c64edc-1f81-4205-b6af-26350d58c888").order("start_time").execute()
    print("\nEstado final dos agendamentos do paciente:")
    for a in check.data:
        s = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
        print(f"  {s} | status={a['status']} | id={a['id']}")

asyncio.run(main())
