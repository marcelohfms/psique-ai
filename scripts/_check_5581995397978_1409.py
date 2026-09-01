import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    pid = "161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"  # Bento Ramos Valença
    appts = (await client.table("appointments").select("*").eq("patient_id", pid).execute()).data
    print("num appts:", len(appts))
    for a in appts:
        print(json.dumps(a, indent=2, ensure_ascii=False, default=str))
        print("-"*40)

asyncio.run(main())
