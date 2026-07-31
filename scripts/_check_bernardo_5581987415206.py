import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    pats = await client.from_("patients").select("*").ilike("name", "%Bernardo Lima%").execute()
    for p in pats.data:
        print("PATIENT", {k: p[k] for k in ("id","name","birth_date","cpf") if k in p})
        ap = await client.from_("appointments").select("*").eq("patient_id", p["id"]).execute()
        for a in ap.data:
            print("  APPT", a)
        pay = await client.from_("payments").select("*").eq("patient_id", p["id"]).execute()
        for x in pay.data:
            print("  PAY", x)
        ct = await client.from_("patient_contacts").select("*").eq("patient_id", p["id"]).execute()
        for c in ct.data:
            print("  CONTACT", c)

asyncio.run(main())
