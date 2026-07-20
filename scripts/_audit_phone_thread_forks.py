import asyncio
from dotenv import load_dotenv
import os
import re
load_dotenv()


async def main():
    import psycopg
    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True) as conn:
        conn.prepare_threshold = None
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
                ("%@s.whatsapp.net",),
            )
            rows = [r[0] for r in await cur.fetchall()]

    print(f"total distinct thread_ids com checkpoint: {len(rows)}")

    # thread_id = <digits>@s.whatsapp.net ; digits = 55 + DDD(2) + local(8 ou 9)
    by_digits = {}
    for t in rows:
        m = re.match(r"^(\d+)@s\.whatsapp\.net$", t)
        if not m:
            continue
        digits = m.group(1)
        by_digits[digits] = t

    forks = []
    for digits in by_digits:
        if len(digits) == 12 and digits.startswith("55"):
            canonical = digits[:4] + "9" + digits[4:]
            if canonical in by_digits:
                forks.append((digits, canonical))

    print(f"\npares legado(12) + canônico(13) ambos com checkpoint: {len(forks)}")
    for legacy, canon in forks:
        print(f"  {legacy}  <->  {canon}")


asyncio.run(main())
