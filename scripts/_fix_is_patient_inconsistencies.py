import asyncio
from dotenv import load_dotenv
load_dotenv()

INCONSISTENT_IDS = [
    "31cce732-e49f-4573-ac8b-17d77255d5e3",  # Andreza → Antônio Teles
    "f8a7b797-2727-4260-bf26-3a7adefb0e2a",  # João Souza → Eva Cristina
    "3e08a15c-f781-441e-938b-aa2e53a1b405",  # Dilenia Van Der Linden → Clara
    "8bebfe6e-5134-404e-83a9-a649400de211",  # Ana Raquel → Sofia Damaso
    "3ef6ca62-6ef6-44a3-955b-d9bd8f81c3e2",  # Ednara → João Pedro
]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for uid in INCONSISTENT_IDS:
        r = await client.from_("users").select("name, patient_name").eq("id", uid).single().execute()
        await client.from_("users").update({"is_patient": False}).eq("id", uid).execute()
        print(f"✅ {r.data['name']} → paciente: {r.data['patient_name']} | is_patient=False")

asyncio.run(main())
