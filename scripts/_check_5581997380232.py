import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581997380232"
# variante sem o 9 extra (formato 558197380232)
PHONE_ALT = "558197380232"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contacts = await client.from_("contacts").select("*").in_("phone", [PHONE, PHONE_ALT]).execute()
    print(f"contacts found: {len(contacts.data)}")
    for c in contacts.data:
        print(" contact:", c)

        links = await client.from_("patient_contacts").select("*").eq("contact_id", c["id"]).execute()
        print(f"  patient_contacts: {len(links.data)}")
        for link in links.data:
            print("   link:", link)
            patient = await client.from_("patients").select("*").eq("id", link["patient_id"]).execute()
            for p in patient.data:
                print("   patient:", p)
                appts = await client.from_("appointments").select("*").eq("patient_id", p["id"]).order("created_at").execute()
                print(f"    appointments: {len(appts.data)}")
                for a in appts.data:
                    print("     ", a)

asyncio.run(main())
