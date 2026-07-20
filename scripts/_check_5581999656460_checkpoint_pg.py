import asyncio
from dotenv import load_dotenv
import os
load_dotenv()

async def main():
    import psycopg
    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str) as conn:
        async with conn.cursor() as cur:
            for phone in ["5581999656460", "558199656460"]:
                await cur.execute(
                    "SELECT thread_id, checkpoint_id FROM checkpoints WHERE thread_id = %s ORDER BY checkpoint_id DESC LIMIT 3",
                    (phone,),
                )
                rows = await cur.fetchall()
                print(f"thread_id={phone}: {len(rows)} checkpoint rows -> {rows}")

asyncio.run(main())
