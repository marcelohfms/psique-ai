import asyncio
from dotenv import load_dotenv
load_dotenv()
async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    p = await c.from_("patients").select("name,birth_date,age,is_returning_patient,custom_price").eq("id","161c1e7f-c4f0-4e56-82f6-4ab2d7b11550").maybe_single().execute()
    print(p.data)
asyncio.run(main())
