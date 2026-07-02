import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_events():
    from supabase import AsyncClient, acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    phone = "5581996937559"

    print(f"=== EVENTOS PARA {phone} ===\n")

    # Get all events
    result = await client.from_("events").select("*").eq("phone", phone).order("created_at").execute()
    events = result.data or []

    print(f"Total de eventos: {len(events)}\n")

    for event in events:
        created = event.get("created_at", "").split("T")
        date_time = f"{created[0]} {created[1][:8]}" if len(created) > 1 else created[0]
        event_type = event.get("event_type")
        metadata = event.get("metadata") or {}

        print(f"[{date_time}] {event_type}")
        if metadata:
            for key, value in metadata.items():
                print(f"  - {key}: {value}")
        print()

asyncio.run(check_events())
