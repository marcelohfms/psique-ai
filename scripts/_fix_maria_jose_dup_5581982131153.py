"""Caso Maria José Alves de Farias (nasc. 20/08/1956) — 10/08/2026.

O contato novo 5581982131153 agendou para a Maria José dizendo que ela JÁ estava
em acompanhamento. A Eva gravou is_returning_patient=True mas criou um paciente
NOVO (ed4ba734), porque upsert_user só resolvia por telefone — o cadastro real
(8ec24e7f, criado 23/06 sob o telefone da nora 5581981139373, consulta completed
em 03/07) nunca foi encontrado. A correção de código (reconciliação por nome
normalizado + nascimento) já está no fluxo; este script corrige RETROATIVAMENTE
o caso que a motivou:

  1. Reponta os appointments do duplicado para o paciente real (mesma linha de
     appointment — taxa/pagamento/Calendar ficam intactos, nada de appointment
     novo).
  2. Copia para o paciente real os campos coletados nesta conversa que ele ainda
     não tem (só onde o real está nulo — o cadastro real é o autoritativo).
  3. Mescla o duplicado via app.patients.merge_duplicate_patient (reponta os
     vínculos de patient_contacts e apaga o duplicado; recusa se ainda houver
     appointment no duplicado).
  4. Conserta o checkpoint do LangGraph do thread: user_db_id → id real
     (senão a conversa segue presa ao id apagado).

Uso:
  uv run python scripts/_fix_maria_jose_dup_5581982131153.py            # dry-run
  uv run python scripts/_fix_maria_jose_dup_5581982131153.py --execute
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

DUP_ID = "ed4ba734-e5df-4843-bb02-0059e2a09352"   # criado 10/08 pelo contato novo
REAL_ID = "8ec24e7f-4998-4a2a-aca5-c3f65eec9ad2"  # cadastro real, 23/06, nora
PHONE = "5581982131153@s.whatsapp.net"            # thread do contato novo

EXECUTE = "--execute" in sys.argv

# Campos que a conversa nova pode ter coletado e o cadastro real pode não ter.
# Copiados APENAS onde o real está nulo — nunca sobrescreve dado do cadastro real.
_COPY_IF_MISSING = (
    "email", "age", "birth_date", "is_returning_patient", "patient_cpf",
    "consultation_reason", "referral_professional",
    "financial_name", "financial_cpf", "financial_email",
)


async def fix_db():
    from app.database import get_supabase
    from app.patients import normalize_person_name, merge_duplicate_patient

    client = await get_supabase()

    dup_rows = (await client.from_("patients").select("*").eq("id", DUP_ID).execute()).data or []
    real_rows = (await client.from_("patients").select("*").eq("id", REAL_ID).execute()).data or []
    if not real_rows:
        print(f"ABORT: paciente real {REAL_ID} não encontrado — nada feito.")
        return False
    if not dup_rows:
        print(f"Duplicado {DUP_ID} não existe mais — já corrigido? Nada feito.")
        return False
    dup, real = dup_rows[0], real_rows[0]

    print(f"Duplicado: {dup['id']}  name={dup.get('name')!r}  birth={dup.get('birth_date')!r}")
    print(f"Real:      {real['id']}  name={real.get('name')!r}  birth={real.get('birth_date')!r}")

    # Sanity: é a mesma pessoa (nome normalizado + nascimento), como no fluxo novo.
    if normalize_person_name(dup.get("name")) != normalize_person_name(real.get("name")):
        print("ABORT: nomes normalizados diferem — não parece o mesmo paciente.")
        return False
    from app.patients import _birth_date_variants
    if dup.get("birth_date") and real.get("birth_date") and \
            real.get("birth_date") not in _birth_date_variants(dup["birth_date"]):
        print("ABORT: datas de nascimento diferem — possível homônimo.")
        return False

    appts = (
        await client.from_("appointments").select("*").eq("patient_id", DUP_ID).execute()
    ).data or []
    print(f"\nAppointments no duplicado: {len(appts)}")
    for a in appts:
        print(f"  - {a.get('appointment_id')}  {a.get('start_time')}  status={a.get('status')}  "
              f"taxa_paga={a.get('booking_fee_paid_at')}")

    to_copy = {
        f: dup[f] for f in _COPY_IF_MISSING
        if dup.get(f) is not None and real.get(f) is None
    }
    print(f"\nCampos a copiar para o real (real está nulo): {to_copy or '(nenhum)'}")

    links = (
        await client.from_("patient_contacts").select("*").eq("patient_id", DUP_ID).execute()
    ).data or []
    print(f"Vínculos do duplicado a repontar: "
          f"{[(l.get('contact_id'), l.get('role')) for l in links]}")

    if not EXECUTE:
        print("\nDRY-RUN — nada foi alterado. Rode com --execute para aplicar.")
        return False

    for a in appts:
        await client.from_("appointments").update(
            {"patient_id": REAL_ID}
        ).eq("appointment_id", a["appointment_id"]).execute()
        print(f"OK: appointment {a['appointment_id']} repontado para {REAL_ID}")

    if to_copy:
        await client.from_("patients").update(to_copy).eq("id", REAL_ID).execute()
        print(f"OK: campos copiados para o real: {list(to_copy)}")

    merged = await merge_duplicate_patient(DUP_ID, REAL_ID)
    if not merged:
        print("ABORT: merge_duplicate_patient recusou (appointment restante no duplicado?).")
        return False
    print(f"OK: vínculos repontados e duplicado {DUP_ID} apagado.")
    return True


async def fix_checkpoint():
    """user_db_id do thread ainda aponta para o duplicado apagado — trocar."""
    import os
    import psycopg
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    # prepare_threshold=None: obrigatório em scripts one-off (pgbouncer).
    async with await psycopg.AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None
    ) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": PHONE}}

        snapshot = await graph.aget_state(config)
        current = (snapshot.values or {}).get("user_db_id")
        print(f"\nCheckpoint: user_db_id={current!r}  next={snapshot.next!r}")
        if current != DUP_ID:
            print("Checkpoint não aponta para o duplicado — nada a corrigir.")
            return
        # Releia `next` antes de mexer: com nós pendentes, um aupdate_state
        # concorrente pode corromper o turno em andamento.
        if snapshot.next:
            print(f"AVISO: thread tem nós pendentes ({snapshot.next}) — checkpoint NÃO "
                  "alterado. Rode de novo quando a conversa estiver ociosa.")
            return
        if not EXECUTE:
            print(f"DRY-RUN: trocaria user_db_id {DUP_ID} → {REAL_ID}.")
            return
        await graph.aupdate_state(config, {"user_db_id": REAL_ID})
        print(f"OK: checkpoint user_db_id → {REAL_ID}")


async def main():
    changed = await fix_db()
    # No dry-run sempre inspeciona o checkpoint; no execute, só depois do merge.
    if not EXECUTE or changed:
        await fix_checkpoint()


asyncio.run(main())
