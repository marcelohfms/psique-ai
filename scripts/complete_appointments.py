"""
Mark past appointments as 'completed'.
Runs via GitHub Actions on a schedule.

Updates all appointments where:
  - status = 'scheduled'
  - end_time < now()
"""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()


async def main():
    from supabase import acreate_client

    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    client = await acreate_client(url, key)

    now_iso = datetime.now(timezone.utc).isoformat()
    result = await (
        client.from_("appointments")
        .update({"status": "completed", "updated_at": now_iso})
        .eq("status", "scheduled")
        .lt("end_time", now_iso)
        .execute()
    )
    count = len(result.data) if result.data else 0
    print(f"Marked {count} appointment(s) as completed.")


if __name__ == "__main__":
    asyncio.run(main())
