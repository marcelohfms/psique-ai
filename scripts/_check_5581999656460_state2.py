import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    phones = ["5581999656460", "558199656460"]

    for phone in phones:
        for table in ["checkpoints", "checkpoint_writes", "checkpoint_blobs"]:
            try:
                cp = await client.from_(table).select("thread_id,checkpoint_id" if table != "checkpoint_blobs" else "*").eq("thread_id", phone).order("checkpoint_id" if table != "checkpoint_blobs" else "channel", desc=True).limit(3).execute()
                print(f"{table} thread_id={phone}: {len(cp.data)} rows")
            except Exception as e:
                print(f"{table} thread_id={phone}: error {e}")

asyncio.run(main())
