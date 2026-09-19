"""Situação do lead: reconcilia as labels do funil no Chatwoot e cutuca quem
abandonou o cadastro. Roda a cada 30 min via GitHub Actions.

Regras (ver app/lead_stall.py):
- Reconcilia label de TODO lead recente, mas só chama o Chatwoot quando a
  situação muda (memória via evento lead_label_set) — protege do rate limit.
- Nudge só para cadastro-abandonado, ativo, dentro da janela de 24h, 8h-20h
  Recife, 1 vez por lead (evento lead_stall_nudge_sent). Espelha o
  send_scheduling_stall_nudges.
- Nada toca eva-ativa/eva-inativa nem o webhook/grafo.
"""
import asyncio
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.lead_stall import (
    evaluate_leads, label_ops, needs_label_change,
    LABEL_CADASTRO, LABEL_SET_EVENT, NUDGE_EVENT,
)
from app.database import log_event, get_events_by_type
from app.chatwoot import get_conversation_id, find_or_create_conversation, set_labels
# Helpers já batidos no cron de pagamento (normalizam dígitos→JID internamente).
from scripts.send_payment_reminders import _window_open, send_whatsapp, save_to_checkpoint

TZ = ZoneInfo("America/Recife")

WINDOW_START = 8   # não cutuca antes das 8h
WINDOW_END = 20    # nem depois das 20h


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def cadastro_nudge_message(contact_first_name: str) -> str:
    saudacao = f"Oi, {contact_first_name}! " if contact_first_name else "Oi! "
    return (
        f"{saudacao}😊 Vi que a gente começou seu cadastro aqui na Clínica Psique "
        f"mas não chegou a finalizar. Quer continuar de onde a gente parou?\n\n"
        f"É rapidinho, é só me responder por aqui. 🙏"
    )


async def _reconcile_label(rec: dict) -> None:
    """Ajusta a label do lead no Chatwoot só quando a situação mudou desde a última
    vez que setamos (memória via evento lead_label_set)."""
    phone = rec["phone"]
    situation = rec["situation"]

    events = await get_events_by_type(phone, LABEL_SET_EVENT, limit=1)
    last_set = (events[0].get("metadata") or {}).get("label") if events else None
    if not needs_label_change(situation, last_set):
        return

    conv_id = get_conversation_id(phone)
    if conv_id is None:
        try:
            conv_id = await find_or_create_conversation(phone)
        except Exception as e:
            print(f"  [label] sem conversa Chatwoot para {phone}: {type(e).__name__}: {e}")
            return

    add, remove = label_ops(situation)
    try:
        await set_labels(conv_id, add=add, remove=remove)
    except Exception as e:
        print(f"  [label] set_labels falhou para {phone}: {type(e).__name__}: {e}")
        return  # não marca — próximo run tenta de novo

    await log_event(LABEL_SET_EVENT, phone, {"label": situation, "conversation_id": conv_id})
    print(f"  [label] {phone}: {last_set} -> {situation}")


async def _maybe_nudge(client, graph, rec: dict, now: datetime) -> None:
    """Cutuca uma vez quem abandonou o cadastro, se ativo e na janela de 24h."""
    if rec["situation"] != LABEL_CADASTRO:
        return
    phone = rec["phone"]
    user = rec["user"]

    if await get_events_by_type(phone, NUDGE_EVENT, limit=1):
        return  # já cutucado
    if not user.get("active", True):
        return  # pausado → relatório da clínica cuida
    if not await _window_open(client, phone, now):
        return  # frio (fora das 24h) → relatório da clínica cuida

    name = user.get("name") or ""
    doctor_key = user.get("preferred_doctor") or ""
    text = cadastro_nudge_message(_first_name(name))

    try:
        await send_whatsapp(phone, text)
    except Exception as e:
        print(f"  [nudge] FALHOU para {phone}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return  # não marca — próximo run tenta de novo

    if graph:
        try:
            await save_to_checkpoint(graph, phone, text, name, doctor_key)
        except Exception as e:
            print(f"  [nudge] checkpoint falhou para {phone}: {type(e).__name__}: {e}")

    await log_event(NUDGE_EVENT, phone, {"last_msg_at": rec["last_msg_at"].isoformat()})
    print(f"  [nudge] enviado para {phone}")


async def main() -> None:
    from supabase import acreate_client

    now = datetime.now(TZ)
    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    records = await evaluate_leads(client, now)
    print(f"Leads recentes avaliados: {len(records)}")

    # Reconciliação de label roda sempre (barata: só escreve quando muda).
    for rec in records:
        await _reconcile_label(rec)

    # Nudge só dentro da janela de horário.
    if not (WINDOW_START <= now.hour < WINDOW_END):
        print(f"Fora da janela de nudge ({WINDOW_START}h–{WINDOW_END}h). Só reconciliei labels.")
        return

    # Checkpointer do LangGraph (mesmo padrão do cron de pagamento).
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        pg_conn = await AsyncConnection.connect(
            conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
        )
        graph = build_graph(checkpointer=AsyncPostgresSaver(pg_conn))
    else:
        print("SUPABASE_CONNECTION_STRING não setado — nudge não vai para o checkpoint.")

    try:
        for rec in records:
            await _maybe_nudge(client, graph, rec, now)
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
