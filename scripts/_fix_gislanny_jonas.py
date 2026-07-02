import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    res = await client.table("patient_contacts").update({
        "is_self": False,
        "relationship": "esposa",
    }).eq("contact_id", "77b64d89-0e8e-43c0-a16a-72ef72103a22").eq("patient_id", "1a878993-843c-4cfa-a6e5-9ec720b4dd89").execute()
    print("UPDATED:", res.data)

asyncio.run(main())
