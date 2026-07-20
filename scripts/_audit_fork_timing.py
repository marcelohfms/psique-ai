import asyncio
from dotenv import load_dotenv
import os
import re
import uuid
from datetime import datetime, timezone
load_dotenv()

FIX_DATE = datetime(2026, 6, 11, tzinfo=timezone.utc)


_V6_EPOCH_OFFSET_100NS = 0x01B21DD213814000  # 1582-10-15 -> 1970-01-01, in 100ns units


def checkpoint_id_to_dt(checkpoint_id: str):
    """LangGraph checkpoint_id is a UUIDv6 (langgraph.checkpoint.base.id.uuid6)."""
    try:
        u = uuid.UUID(checkpoint_id)
        time_high_and_mid = u.int >> 80
        time_low = (u.int >> 64) & 0x0FFF
        timestamp_100ns = (time_high_and_mid << 12) | time_low
        unix_100ns = timestamp_100ns - _V6_EPOCH_OFFSET_100NS
        return datetime.fromtimestamp(unix_100ns / 1e7, tz=timezone.utc)
    except Exception:
        return None


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

            legacy_after_fix = []
            legacy_before_fix_only = []
            for legacy, canon in forks:
                await cur.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s ORDER BY checkpoint_id DESC LIMIT 1",
                    (legacy + "@s.whatsapp.net",),
                )
                row = await cur.fetchone()
                last_legacy_dt = checkpoint_id_to_dt(row[0]) if row else None

                await cur.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s ORDER BY checkpoint_id ASC LIMIT 1",
                    (canon + "@s.whatsapp.net",),
                )
                row2 = await cur.fetchone()
                first_canon_dt = checkpoint_id_to_dt(row2[0]) if row2 else None

                if last_legacy_dt and last_legacy_dt > FIX_DATE:
                    legacy_after_fix.append((legacy, canon, last_legacy_dt, first_canon_dt))
                else:
                    legacy_before_fix_only.append((legacy, canon, last_legacy_dt, first_canon_dt))

    print(f"total forks: {len(forks)}")
    print(f"\nlegado com atividade DEPOIS do fix de 11/06 (split continua acontecendo): {len(legacy_after_fix)}")
    for legacy, canon, last_l, first_c in sorted(legacy_after_fix, key=lambda x: x[2] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:20]:
        print(f"  {legacy}<->{canon}  ultima_msg_legado={last_l}  primeira_msg_canonico={first_c}")

    print(f"\nlegado só ativo ANTES do fix (débito histórico, não recorrente): {len(legacy_before_fix_only)}")


asyncio.run(main())
