import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    # Busca qualquer variação de "felipe de paula" no banco
    for term in ["felipe de paula", "felipe", "paula"]:
        r = await client.from_("users").select("id, number, name, patient_name, is_patient").ilike("patient_name", f"%{term}%").execute()
        for row in r.data:
            if "paula" in (row.get("patient_name") or "").lower() or "de paula" in (row.get("patient_name") or "").lower():
                print("FOUND:", row)

asyncio.run(main())
