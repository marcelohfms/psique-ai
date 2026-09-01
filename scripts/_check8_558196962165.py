import asyncio
from dotenv import load_dotenv
load_dotenv()

SUZI = "3b0a5557-bef2-4c22-b416-5f564deb7592"

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    print("=== payments sample (any row) ===")
    p = await client.table("payments").select("*").limit(1).execute()
    if p.data:
        print("columns:", list(p.data[0].keys()))
    print("\n=== payments for SUZI ===")
    p2 = await client.table("payments").select("*").eq("patient_id", SUZI).execute()
    for row in p2.data:
        print(row)
    print("\n=== events for SUZI ===")
    try:
        e = await client.table("events").select("*").eq("patient_id", SUZI).execute()
        for row in e.data:
            print(row.get("created_at"), row.get("event_type"), str(row.get("metadata"))[:150])
    except Exception as ex:
        print("events err:", ex)

asyncio.run(main())
