"""Para cada conversa com comprovante e cadastro incompleto, aponta qual campo
REALMENTE bloqueia is_registration_complete() (o naive "todos os None" engana:
guardian_* só é exigido para menores com terceiro agendando)."""
import asyncio, os
from dotenv import load_dotenv
load_dotenv()


def blocking_field(u: dict) -> str | None:
    from app.database import DOCTOR_IDS
    for f in ("name", "email", "birth_date", "doctor_id"):
        if not u.get(f):
            return f
    if u.get("is_patient") is None:
        return "is_patient"
    age = u.get("age")
    if age is not None and age < 18 and u.get("doctor_id") == DOCTOR_IDS.get("julio") \
            and u.get("is_returning_patient") is None:
        return "is_returning_patient (menor Júlio)"
    if u.get("is_patient") is False and not u.get("patient_name"):
        return "patient_name"
    if age is not None and age < 18 and u.get("is_patient") is False:
        req = ["guardian_name", "guardian_relationship"]
        if u.get("is_returning_patient") is False:
            req.append("guardian_cpf")
        for f in req:
            if not u.get(f):
                return f
    return None


async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.database import DOCTOR_IDS, get_supabase

    supa = await get_supabase()
    conn = await AsyncConnection.connect(
        os.environ["SUPABASE_CONNECTION_STRING"],
        autocommit=True, prepare_threshold=None, row_factory=dict_row,
    )
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
            threads = [r["thread_id"] for r in await cur.fetchall()]
        cp = AsyncPostgresSaver(conn)
        from collections import Counter
        reasons = Counter()
        rows = []
        for t in threads:
            tup = await cp.aget_tuple({"configurable": {"thread_id": t}})
            if not tup:
                continue
            v = tup.checkpoint.get("channel_values", {})
            msgs = v.get("messages") or []
            n_receipts = sum(
                1 for m in msgs
                if getattr(m, "type", "") == "human"
                and "COMPROVANTE DE PAGAMENTO" in str(getattr(m, "content", "") or "")
            )
            if not n_receipts:
                continue
            u = {
                "name": v.get("user_name"), "email": v.get("patient_email"),
                "birth_date": v.get("birth_date"),
                "doctor_id": DOCTOR_IDS.get(v.get("preferred_doctor", ""), None),
                "is_patient": v.get("is_patient"),
                "is_returning_patient": v.get("is_returning_patient"),
                "patient_name": v.get("patient_name"), "age": v.get("patient_age"),
                "guardian_name": v.get("guardian_name"),
                "guardian_cpf": v.get("guardian_cpf"),
                "guardian_relationship": v.get("guardian_relationship"),
            }
            b = blocking_field(u)
            if not b:
                continue
            reasons[b] += 1
            phone = t.replace("@s.whatsapp.net", "")
            ev = await supa.from_("events").select("event_type").eq("phone", phone).eq(
                "event_type", "payment_receipt_registered").limit(1).execute()
            rows.append((phone, v.get("stage"), b, n_receipts, bool(ev.data)))

        print(f"=== conversas com comprovante + cadastro bloqueado: {len(rows)} ===")
        for phone, stage, b, n, registered in sorted(rows, key=lambda r: (r[4], r[2])):
            flag = "ok" if registered else "SEM REGISTRO DE PAGAMENTO"
            print(f"  {phone:16} stage={stage:13} bloqueio={b:32} comprovantes={n:2} {flag}")
        print("\n=== campo que bloqueia ===")
        for k, n in reasons.most_common():
            print(f"  {n:3}x {k}")

asyncio.run(main())
