import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("appointments").update({
        "modality": "presencial",
    }).eq("appointment_id", "mi87btfgmui0kmp6ita6pkf5qo").execute()
    print("UPDATED:", res.data)

asyncio.run(main())
