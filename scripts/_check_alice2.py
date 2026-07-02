import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Eventos para o número
    for phone in ["5587981054742", "5587981054742@s.whatsapp.net"]:
        events = await client.from_("events").select("event_type, metadata, created_at") \
            .ilike("phone", f"%981054742%").order("created_at", desc=True).limit(15).execute()
        if events.data:
            print("=== EVENTOS ===")
            for e in reversed(events.data):
                ts = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                print(f"  {ts} | {e['event_type']} | {str(e.get('metadata',''))[:150]}")
            break

asyncio.run(main())
