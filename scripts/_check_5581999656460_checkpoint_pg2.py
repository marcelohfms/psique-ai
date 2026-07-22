import asyncio
from dotenv import load_dotenv
import os
load_dotenv()

async def main():
    import psycopg
    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str) as conn:
        async with conn.cursor() as cur:
            candidates = [
                "5581999656460@s.whatsapp.net",
                "558199656460@s.whatsapp.net",
                "5581999656460",
                "558199656460",
            ]
            for phone in candidates:
                await cur.execute(
                    "SELECT thread_id, checkpoint_id, checkpoint_ns FROM checkpoints WHERE thread_id = %s ORDER BY checkpoint_id DESC LIMIT 5",
                    (phone,),
                )
                rows = await cur.fetchall()
                print(f"thread_id={phone!r}: {len(rows)} checkpoint rows -> {rows}")

            # Also: distinct thread_ids that look like this phone (LIKE search)
            await cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
                ("%9656460%",),
            )
            like_rows = await cur.fetchall()
            print(f"\nLIKE %9656460%: {like_rows}")

asyncio.run(main())
