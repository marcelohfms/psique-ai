import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    res = await client.table("patients").select("id,name,patient_cpf").not_.is_("patient_cpf", "null").limit(10).execute()
    print("patients with cpf:", res.data)

    res2 = await client.table("payments").select("*").eq("drive_link", "https://drive.google.com/file/d/1aFfDzctpC2aGq67AqmgjE5TCwcIOgB6R/view?usp=drivesdk").execute()
    print("payment row for this receipt:", res2.data)

    # check payments table schema by fetching one row
    res3 = await client.table("payments").select("*").limit(2).execute()
    print("sample payments rows:", res3.data)

asyncio.run(main())
