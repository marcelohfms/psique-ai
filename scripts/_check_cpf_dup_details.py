import asyncio
from dotenv import load_dotenv
load_dotenv()

PAIRS = [
    ("fa35a5c5-7114-4ca1-a187-49b159a6203e", "550a6166-0629-4297-a6a6-cdf2eb2033af"),
    ("91ce9a16-620e-481b-a0fc-c5c5fb8d1636", "e29d73e8-c9aa-4452-8586-d67f8d744fc7"),
    ("0a150731-21c6-4ed2-9132-4443f3c0fd13", "caf9b30f-a771-4deb-877f-1fd077233d15"),
    ("7636cd7b-93e5-4426-92d5-a8ff43e34fb8", "36d55bea-14c4-4039-839c-560ffb2475ec"),
    ("79520693-d160-4daf-9acb-705ececa157a", "01e2f200-7d36-48ba-9c82-e7117a0a28ab"),
    ("c50c7570-845b-443d-bb10-661afbbdfd15", "b2f15c07-cab5-489a-92ba-47089863bab1"),
    ("8d4a752f-4529-4d1f-a096-63d05572fe63", "53e6f62d-8ae8-4a7f-934f-8a11c8fdff11"),
]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for a, b in PAIRS:
        print(f"\n=== PAIR {a} / {b} ===")
        for pid in (a, b):
            p = await client.from_("patients").select("id, name, birth_date, patient_cpf").eq("id", pid).execute()
            pc = await client.from_("patient_contacts").select("contact_id, role, relationship, contacts(name, phone)").eq("patient_id", pid).execute()
            appts = await client.from_("appointments").select("appointment_id").eq("patient_id", pid).execute()
            print(f"  {p.data}")
            for link in pc.data:
                print(f"    contact={link.get('contacts')} role={link.get('role')} rel={link.get('relationship')}")
            print(f"    appointments count={len(appts.data)}")

asyncio.run(main())
