import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for aid in ["4r9hao1o9t9d1phv5ob1sqp00g","ug0n046jqs1o65296e6aaetfuk","bdllbkc5qke95ldrl6r8bqk634"]:
        pays = await client.table("payments").select("*").eq("appointment_id",aid).execute()
        print(f"=== PAYMENTS for {aid} ===")
        for p in pays.data:
            print(p)
asyncio.run(main())
