import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT_ID = "d09ca470-ed0b-4050-a76d-7ad4caf12afa"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    before = await client.from_("appointments").select("id, consultation_type").eq("id", APPT_ID).execute()
    print("antes:", before.data)

    await client.from_("appointments").update({"consultation_type": "primeira_consulta"}).eq("id", APPT_ID).execute()

    after = await client.from_("appointments").select("id, consultation_type").eq("id", APPT_ID).execute()
    print("depois:", after.data)

asyncio.run(main())
