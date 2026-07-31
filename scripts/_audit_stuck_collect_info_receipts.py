"""Audita conversas presas em stage=collect_info que receberam comprovante.

Quando is_registration_complete() falha (ex: guardian_relationship vazio), o
_route_entry do graph força todo turno de volta para collect_info — um nó SEM
ferramentas. Comprovantes que chegam nesse estado nunca viram register_payment.
"""
import asyncio, os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")


async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.database import is_registration_complete, DOCTOR_IDS

    conn = await AsyncConnection.connect(
        os.environ["SUPABASE_CONNECTION_STRING"],
        autocommit=True, prepare_threshold=None, row_factory=dict_row,
    )
    async with conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
            threads = [r["thread_id"] for r in await cur.fetchall()]
        print(f"threads: {len(threads)}")

        cp = AsyncPostgresSaver(conn)
        stuck = []
        for t in threads:
            tup = await cp.aget_tuple({"configurable": {"thread_id": t}})
            if not tup:
                continue
            v = tup.checkpoint.get("channel_values", {})
            msgs = v.get("messages") or []
            receipts = [
                m for m in msgs
                if "COMPROVANTE DE PAGAMENTO" in str(getattr(m, "content", "") or "")
                and getattr(m, "type", "") == "human"
            ]
            if not receipts:
                continue
            reg = {
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
            complete = is_registration_complete(reg)
            if complete:
                continue
            missing = [k for k, val in reg.items() if val is None or val == ""]
            stuck.append((t, v.get("stage"), v.get("patient_name"), missing, len(receipts)))

        print(f"\n=== conversas com comprovante E cadastro incompleto: {len(stuck)} ===")
        for t, stage, name, missing, n in stuck:
            print(f"  {t} stage={stage} paciente={name!r} comprovantes={n} faltando={missing}")

asyncio.run(main())
