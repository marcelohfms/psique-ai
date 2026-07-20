import asyncio
from dotenv import load_dotenv
load_dotenv()

CONTACT_ID = "bd84c191-a673-459f-a75d-5478fc646c49"
PATIENT_ID = "fcf32f48-ee0d-4b05-8287-0569991802a0"


async def main():
    from datetime import date
    from app.database import get_supabase
    client = await get_supabase()

    today = date.today()
    bd = date(1943, 2, 2)
    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))

    await client.from_("contacts").update({"name": "Zildo"}).eq("id", CONTACT_ID).execute()
    await client.from_("patients").update({
        "name": "Josefa Eugênio Pereira",
        "birth_date": "02/02/1943",
        "age": age,
    }).eq("id", PATIENT_ID).execute()
    await client.from_("patient_contacts").update({"relationship": "filho"}).eq("contact_id", CONTACT_ID).eq("patient_id", PATIENT_ID).execute()

    print(f"OK. Idade recalculada: {age}")

    # confirma
    c = await client.from_("contacts").select("*").eq("id", CONTACT_ID).execute()
    p = await client.from_("patients").select("*").eq("id", PATIENT_ID).execute()
    pc = await client.from_("patient_contacts").select("*").eq("contact_id", CONTACT_ID).execute()
    print("\ncontacts:", c.data)
    print("\npatients:", p.data)
    print("\npatient_contacts:", pc.data)

asyncio.run(main())
