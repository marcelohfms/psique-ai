import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Busca usuários com número de 12 dígitos (brasileiro sem o 9 extra)
    r = await client.from_("users").select("id, number, name, patient_name").execute()
    short = [u for u in r.data if len((u.get("number") or "").replace("@s.whatsapp.net", "")) == 12
             and (u.get("number") or "").startswith("55")]
    print(f"Usuários com 12 dígitos: {len(short)}")
    for u in short:
        print(f"  {u['number']} | {u.get('patient_name') or u.get('name')}")

asyncio.run(main())
