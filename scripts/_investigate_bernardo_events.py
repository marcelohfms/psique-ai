import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    
    # Check events table
    events = await client.from_("events").select("*").eq("phone", "5581987415206").order("created_at").execute()
    print("=== EVENTS ===")
    for e in events.data:
        dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        print(f"{dt} | {e['event_type']} | {e.get('data')}")
    
    # Check patient record for guardian fields
    patient = await client.from_("patients").select("*").eq("name", "Bernardo Lima Beltrão Teixeira").single().execute()
    print("\n=== PATIENT RECORD ===")
    for k in ["id", "guardian_name", "guardian_cpf", "guardian_relationship"]:
        print(f"{k}: {patient.data.get(k)}")

asyncio.run(main())
