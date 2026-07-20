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

            by_digits = {}
            for t in rows:
                m = re.match(r"^(\d+)@s\.whatsapp\.net$", t)
                if m:
                    by_digits[m.group(1)] = t

            forks = []
            for digits in by_digits:
                if len(digits) == 12 and digits.startswith("55"):
                    canonical = digits[:4] + "9" + digits[4:]
                    if canonical in by_digits:
                        forks.append((digits, canonical))

            # For each fork, count checkpoint rows (proxy for turns) on each side,
            # and check if the canonical side has more than 1 checkpoint (i.e. real
            # back-and-forth, not just a single reminder message).
            both_active = []
            canonical_only_reminder = []
            for legacy, canon in forks:
                await cur.execute(
                    "SELECT count(*) FROM checkpoints WHERE thread_id = %s",
                    (legacy + "@s.whatsapp.net",),
                )
                legacy_count = (await cur.fetchone())[0]
                await cur.execute(
                    "SELECT count(*) FROM checkpoints WHERE thread_id = %s",
                    (canon + "@s.whatsapp.net",),
                )
                canon_count = (await cur.fetchone())[0]
                if legacy_count > 3 and canon_count > 3:
                    both_active.append((legacy, canon, legacy_count, canon_count))
                elif canon_count <= 2:
                    canonical_only_reminder.append((legacy, canon, legacy_count, canon_count))

    print(f"total forks: {len(forks)}")
    print(f"\nfork com AMBOS os lados com atividade real (>3 checkpoints cada): {len(both_active)}")
    for row in both_active[:30]:
        print(" ", row)
    print(f"\nfork onde o lado canônico só tem 1-2 checkpoints (provável só lembrete, sem dano real ainda): {len(canonical_only_reminder)}")


asyncio.run(main())
