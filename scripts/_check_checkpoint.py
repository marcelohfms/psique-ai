import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    PHONE = "5581995302944"

    # Checkpoint do LangGraph
    for tbl in ["checkpoints", "checkpoint_writes", "checkpoint_blobs"]:
        try:
            r = await client.from_(tbl).select("thread_id, created_at").ilike("thread_id", f"%{PHONE}%").limit(5).execute()
            if r.data:
                print(f"{tbl}: {r.data}")
            else:
                print(f"{tbl}: vazio")
        except Exception as e:
            print(f"{tbl}: erro — {e}")

asyncio.run(main())
