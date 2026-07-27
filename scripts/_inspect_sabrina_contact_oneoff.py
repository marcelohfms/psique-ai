import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    CONTACT_ID = "97f577e0-5b82-4a2e-befe-c2aa0aa93a31"
    PATIENT_ID = "d294bfd2-cd7d-468d-a065-18dfcdde9429"

    pcs = (await client.from_("patient_contacts").select("*, patients(name)").eq("contact_id", CONTACT_ID).execute()).data
    print("=== Todos os vínculos desse contato (Sabrina) ===")
    for pc in pcs:
        print(" ", pc)

    appts = (await client.from_("appointments").select("*").eq("patient_id", PATIENT_ID).order("start_time").execute()).data
    print("\n=== Agendamentos de Arthur (patient_id=d294bfd2) ===")
    for a in appts:
        print(" ", a.get("appointment_id"), a.get("start_time"), a.get("status"), "contact_id:", a.get("contact_id"))

asyncio.run(main())
