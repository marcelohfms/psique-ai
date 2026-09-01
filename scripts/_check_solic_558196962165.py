import asyncio
from dotenv import load_dotenv
load_dotenv()

SUZI = "3b0a5557-bef2-4c22-b416-5f564deb7592"
LAILA = "ae408243-53d1-4b62-88a5-f323a6d710df"

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    # inspect documents table schema
    sample = await client.table("documents").select("*").limit(1).execute()
    if sample.data:
        print("documents columns:", list(sample.data[0].keys()))
    for name, pid in [("SUZI", SUZI), ("LAILA", LAILA)]:
        print(f"\n=== DOCUMENTS {name} ===")
        d = await client.table("documents").select("*").eq("patient_id", pid).execute()
        for row in d.data:
            print(row)

asyncio.run(main())
