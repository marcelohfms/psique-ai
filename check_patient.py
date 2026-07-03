import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_patient():
    from supabase import AsyncClient, acreate_client
    
    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )
    
    phone = "5581986458553"
    
    # Check for message_send_failed events
    print(f"=== EVENTS WITH 'send' OR 'error' ===")
    all_events = await client.from_("events").select("*").eq("phone", phone).execute()
    
    for event in all_events.data or []:
        event_type = event.get('event_type', '')
        if 'send' in event_type.lower() or 'error' in event_type.lower() or 'fail' in event_type.lower():
            print(f"{event.get('created_at')[:19]} | {event_type} | {event.get('metadata')}")
    
    print(f"\n=== ALL EVENT TYPES ===")
    event_types = set()
    for event in all_events.data or []:
        event_types.add(event.get('event_type'))
    
    for et in sorted(event_types):
        count = sum(1 for e in (all_events.data or []) if e.get('event_type') == et)
        print(f"  {et}: {count}")

asyncio.run(check_patient())
