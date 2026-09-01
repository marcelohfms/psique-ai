import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONES = ["558196962165", "5581996962165", "8196962165"]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for phone in PHONES:
        contact = await client.table("contacts").select("*").eq("phone", phone).execute()
        for c in contact.data:
            print(f"=== CONTACT {phone} ===")
            print(" id:", c.get("id"), "name:", c.get("name"), "cpf:", c.get("cpf"), "active:", c.get("active"))
            pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", c["id"]).execute()
            for row in pc.data:
                p = row.get("patients") or {}
                print("  role:", row.get("role"), "is_self:", row.get("is_self"), "rel:", row.get("relationship"))
                print("    patient:", p.get("id"), p.get("name"), "dob:", p.get("date_of_birth"), "cpf:", p.get("cpf"))

    print("\n=== LAST MESSAGES ===")
    for phone in PHONES:
        msgs = await client.table("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(60).execute()
        for m in reversed(msgs.data):
            print(f"[{m.get('created_at')}] {m.get('role')}: {(m.get('content') or '')[:300]}")

asyncio.run(main())
