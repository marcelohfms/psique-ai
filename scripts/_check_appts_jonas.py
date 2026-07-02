import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    appts = await client.table("appointments").select("*").eq("patient_id", "1a878993-843c-4cfa-a6e5-9ec720b4dd89").execute()
    print("APPOINTMENTS:", appts.data)

asyncio.run(main())
