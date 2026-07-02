import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    await client.from_("appointments").update({"modality": "online"}) \
        .eq("appointment_id", "cjcljgkitt0tegnc0qq86i1he4").execute()
    print("✅ 15:00 → online")

    await client.from_("appointments").update({"modality": "presencial"}) \
        .eq("appointment_id", "e3l0n643vil0qusub1rf713ki0").execute()
    print("✅ 17:00 → presencial")

asyncio.run(main())
