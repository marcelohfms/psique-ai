import asyncio
from dotenv import load_dotenv
load_dotenv()
async def main():
    from app.database import get_supabase
    client = await get_supabase()
    r = await client.from_("patients").select("id,name,birth_date,custom_price,doctor_id").eq("id","6b82acdd-b1d1-4407-b77f-635118ddf194").single().execute()
    d = r.data
    print("name:", d.get("name"), "| birth_date:", d.get("birth_date"), "| custom_price:", d.get("custom_price"))
    # age at 13/07/2026 from dd/mm/yyyy
    dd,mm,yy = map(int, str(d["birth_date"]).split("/"))
    age = 2026 - yy - ((7,13) < (mm,dd))
    print("age at consult:", age)
asyncio.run(main())
