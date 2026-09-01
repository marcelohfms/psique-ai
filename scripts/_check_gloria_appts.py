import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONES = ["558191270824", "5581991270824"]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # find contacts + patients
    pat_ids = set()
    for p in PHONES:
        c = await client.table("contacts").select("*").eq("phone", p).execute()
        for row in c.data:
            print("CONTACT", {k: row.get(k) for k in ("id","name","phone")})
            cid = row["id"]
            # contact_patients links
            try:
                links = await client.table("contact_patients").select("*").eq("contact_id", cid).execute()
                for l in links.data:
                    print("  LINK", l)
                    pat_ids.add(l.get("patient_id"))
            except Exception as e:
                print("  link err", e)

    # also search patients by name Paula
    pats = await client.table("patients").select("*").ilike("name", "%Paula%").execute()
    print("\n=== PATIENTS name~Paula ===")
    for pt in pats.data:
        print({k: pt.get(k) for k in ("id","name","birth_date","email","phone")})
        pat_ids.add(pt["id"])

    print("\n=== APPOINTMENTS ===")
    for pid in pat_ids:
        if not pid:
            continue
        appts = await client.table("appointments").select("*").eq("patient_id", pid).order("start_time", desc=False).execute()
        for a in appts.data:
            print(pid[:8], "|", a.get("start_time"), "|", a.get("status"), "|", a.get("doctor"), "| fee_paid:", a.get("booking_fee_paid_at"), "| id:", a.get("id"))

asyncio.run(main())
