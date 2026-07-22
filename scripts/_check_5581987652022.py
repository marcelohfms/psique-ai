import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581987652022"
PHONE_ALT = "558187652022"  # sem o 9 adicional

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    contacts = []
    for phone in (PHONE, PHONE_ALT):
        contact = await client.table("contacts").select("*").eq("phone", phone).execute()
        print(f"CONTACT (phone={phone}):", contact.data)
        contacts.extend(contact.data)

    for c in contacts:
        pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
        print("PATIENT_CONTACTS for contact_id=", c["id"])
        for row in pc.data:
            print(" role:", row.get("role"), "is_self:", row.get("is_self"))
            print(" patient:", row.get("patients"))

    for c in contacts:
        conv = await client.table("conversations").select("*").eq("contact_id", c["id"]).execute()
        print("CONVERSATIONS for contact_id=", c["id"], ":", conv.data)

asyncio.run(main())
