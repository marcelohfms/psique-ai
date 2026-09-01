import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    p = await c.from_("patients").select("*").ilike("name","%daniella%").execute()
    print(f"=== patients ilike daniella ({len(p.data or [])}) ===")
    for row in p.data or []:
        print("  ---")
        for k,v in row.items():
            if v not in (None, "", False):
                print(f"    {k}: {v}")
    # tambem qualquer variacao 'daniela'
    p2 = await c.from_("patients").select("id,name,patient_email").ilike("name","%daniela%").execute()
    print(f"\n=== patients ilike daniela (1 l) ({len(p2.data or [])}) ===")
    for row in p2.data or []:
        print(f"   {row.get('name')} | email={row.get('patient_email')!r} | {row.get('id')}")

asyncio.run(main())
