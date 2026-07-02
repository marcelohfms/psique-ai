import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    await client.from_("users").update({
        "name": "Angélica Magaly Zacarias de Melo Costa",
        "guardian_name": "Angélica Magaly Zacarias de Melo Costa",
    }).eq("id", "ea94b961-3f91-4acf-9150-f39fc2f6ff1d").execute()
    print("✅ Nome e guardian_name limpos (removido \\n)")

asyncio.run(main())
