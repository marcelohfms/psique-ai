import asyncio
from dotenv import load_dotenv
load_dotenv()

ANDREANA_PHONE = "5581997006125"
ANDREANA_PHONE_ALT = "558197006125"  # sem o 9 adicional
PAULA_PHONE = "5581999862192"
PAULA_PHONE_ALT = "558199862192"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    print("=" * 80)
    print("CONTACTS by phone")
    print("=" * 80)
    for label, phones in (("ANDREANA", (ANDREANA_PHONE, ANDREANA_PHONE_ALT)),
                          ("PAULA", (PAULA_PHONE, PAULA_PHONE_ALT))):
        for phone in phones:
            res = await client.table("contacts").select("*").eq("phone", phone).execute()
            print(f"[{label}] phone={phone}:", res.data)

    print()
    print("=" * 80)
    print("PATIENTS by name (ilike)")
    print("=" * 80)
    for name in ("%ANDREANA%QUIDUTE%", "%PAULA%QUIDUTE%"):
        res = await client.table("patients").select("*").ilike("name", name).execute()
        print(f"name ilike {name}:", res.data)

    print()
    print("=" * 80)
    print("PATIENT_CONTACTS for contacts found above (with patient + contact detail)")
    print("=" * 80)
    all_phones = [ANDREANA_PHONE, ANDREANA_PHONE_ALT, PAULA_PHONE, PAULA_PHONE_ALT]
    contact_ids = []
    for phone in all_phones:
        res = await client.table("contacts").select("*").eq("phone", phone).execute()
        for c in res.data:
            contact_ids.append(c["id"])

    contact_ids = list(set(contact_ids))
    for cid in contact_ids:
        pc = await client.table("patient_contacts").select("*, patients(*)").eq("contact_id", cid).execute()
        print(f"contact_id={cid}:")
        for row in pc.data:
            print("  role:", row.get("role"), "is_self:", row.get("is_self"), "relationship:", row.get("relationship"))
            print("  patient:", row.get("patients"))

    print()
    print("=" * 80)
    print("APPOINTMENTS for patient_ids linked to these contacts")
    print("=" * 80)
    patient_ids = set()
    for cid in contact_ids:
        pc = await client.table("patient_contacts").select("patient_id").eq("contact_id", cid).execute()
        for row in pc.data:
            patient_ids.add(row["patient_id"])

    for pid in patient_ids:
        appts = await client.table("appointments").select("*").eq("patient_id", pid).order("created_at", desc=True).limit(5).execute()
        print(f"patient_id={pid}:", appts.data)

asyncio.run(main())
