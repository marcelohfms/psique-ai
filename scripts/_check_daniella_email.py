import asyncio
from dotenv import load_dotenv
load_dotenv()

DANIELLA = "970df18e-268c-4454-b3ce-dc50882c9c6b"
BENTO = "161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"
DANIELLA_CONTACT = "2e409f80-2937-4bee-8a26-ebdb1f29e6b7"

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    for pid,l in [(DANIELLA,"Daniella ficha"),(BENTO,"Bento ficha")]:
        p = await c.from_("patients").select("*").eq("id",pid).execute()
        row = (p.data or [{}])[0]
        print(f"=== patients {l} ===  patient_email={row.get('patient_email')!r}")
    ct = await c.from_("contacts").select("*").eq("id",DANIELLA_CONTACT).execute()
    print("\n=== contact Daniella 2e409f80 ===")
    for k,v in (ct.data or [{}])[0].items():
        print(f"  {k}: {v}")

asyncio.run(main())
