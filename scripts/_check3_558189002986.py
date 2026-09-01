import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    PID="2642269e-5ae2-4d88-8866-ab0a154554cd"
    pays = await client.table("payments").select("*").eq("patient_id",PID).execute()
    print("=== PAYMENTS ===")
    for p in pays.data:
        print(p)
asyncio.run(main())
