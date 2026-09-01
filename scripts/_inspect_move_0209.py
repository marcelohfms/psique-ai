import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "uvujd8pjg6aha3a7h6rfegnvmc"
BENTO = "161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"
DANIELLA = "970df18e-268c-4454-b3ce-dc50882c9c6b"

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()

    ap = await c.from_("appointments").select("*").eq("appointment_id",APPT).execute()
    print("=== appointment 02/09 (linha completa) ===")
    for a in ap.data or []:
        for k,v in a.items():
            print(f"  {k}: {v}")

    for pid,label in [(BENTO,"BENTO"),(DANIELLA,"DANIELLA ficha")]:
        p = await c.from_("patients").select("*").eq("id",pid).execute()
        print(f"\n=== patients {label} {pid} ===")
        for row in p.data or []:
            for k in ("id","name","birth_date","custom_price","patient_email","is_minor"):
                if k in row: print(f"  {k}: {row.get(k)}")

    # tabela de vinculo contato<->paciente (descobrir nome real)
    for tbl in ("patient_contacts","patient_contact_roles","contact_patient_roles"):
        try:
            r = await c.from_(tbl).select("*").in_("patient_id",[BENTO,DANIELLA]).execute()
            print(f"\n=== {tbl} (Bento+Daniella) ===")
            for row in r.data or []:
                print("  ", row)
        except Exception as e:
            print(f"  ({tbl} n/a: {str(e)[:60]})")

asyncio.run(main())
