import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    phone = "558199656460"

    # events table
    ev = await client.from_("events").select("*").eq("phone", phone).order("created_at", desc=False).execute()
    print(f"=== events phone={phone}: {len(ev.data)} ===")
    for e in ev.data:
        dt = datetime.fromisoformat(e["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
        print(f"  {dt} | {e.get('event_type')} | {e.get('data')}")

    # checkpoint - langgraph checkpoints table
    for table in ["checkpoints", "checkpoint_writes"]:
        try:
            cp = await client.from_(table).select("*").eq("thread_id", phone).order("checkpoint_id", desc=True).limit(3).execute()
            print(f"\n=== {table} thread_id={phone}: {len(cp.data)} rows (latest 3) ===")
            for row in cp.data:
                print({k: (str(v)[:200] if k != "checkpoint" else "...") for k, v in row.items()})
        except Exception as e:
            print(f"\n=== {table}: error {e}")

asyncio.run(main())
