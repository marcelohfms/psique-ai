"""Nudge de "pediu data e não continuou": cutuca no WhatsApp quem viu horários e
não confirmou. Roda a cada 30 min via GitHub Actions.

Regras (ver app/scheduling_stall.py para a lógica de abandono):
- Só envia entre 8h e 20h (Recife). O agendamento vira "abandonado" 4h após a
  oferta; se esse marco cair fora da janela, o nudge sai no próximo run dentro
  dela (na prática, 8h do dia seguinte). O adiamento é de no máx. ~12h, então o
  paciente ainda está dentro das 24h do WhatsApp — mensagem livre, sem template.
- Só cutuca quem está ATIVO (não eva-inativa/pausado) E dentro da janela de 24h.
  Os demais (pausados, que podem não querer bot; e os frios) NÃO são tocados aqui
  — vão para o e-mail da clínica em send_scheduling_stall_report.py.
- 1 tentativa só por caso: grava o evento scheduling_nudge_sent e nunca repete.
- A mensagem é injetada no checkpoint do LangGraph para a Eva ter contexto quando
  o paciente responder.
"""
import asyncio
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.scheduling_stall import fetch_abandoned, NUDGE_EVENT, mark_handled
# Reusa os helpers de envio/janela/checkpoint já batidos no cron de pagamento.
from scripts.send_payment_reminders import _window_open, send_whatsapp, save_to_checkpoint

TZ = ZoneInfo("America/Recife")

WINDOW_START = 8   # não cutuca antes das 8h
WINDOW_END = 20    # nem depois das 20h


def _first_name(name: str) -> str:
    return (name or "").strip().split(" ")[0] if name else ""


def stall_nudge_message(contact_first_name: str) -> str:
    saudacao = f"Oi, {contact_first_name}! " if contact_first_name else "Oi! "
    return (
        f"{saudacao}😊 Vi que você começou a agendar sua consulta aqui, mas a gente "
        f"não chegou a fechar o horário. Ainda quer marcar?\n\n"
        f"Se sim, é só me dizer o melhor dia e turno que eu já vejo as opções "
        f"disponíveis pra você escolher. 🙏"
    )


async def _send_nudge(client, graph, case: dict, now: datetime) -> None:
    from app.database import get_user_by_phone

    phone = case["phone"]
    user = await get_user_by_phone(phone) or {}

    if not user.get("active"):
        return  # eva-inativa/pausado → e-mail da clínica cuida
    if not await _window_open(client, phone, now):
        return  # frio (fora das 24h) → e-mail da clínica cuida

    name = user.get("name") or ""
    doctor_key = case["metadata"].get("doctor") or user.get("preferred_doctor") or ""
    text = stall_nudge_message(_first_name(name))

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

    await mark_handled(client, phone, NUDGE_EVENT,
                       {"offered_at": case["offered_at"].isoformat()})
    print(f"  [nudge] enviado para {phone}")


async def main() -> None:
    from supabase import acreate_client

    now = datetime.now(TZ)
    if not (WINDOW_START <= now.hour < WINDOW_END):
        print(f"Fora da janela de envio ({WINDOW_START}h–{WINDOW_END}h). Encerrando.")
        return

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    cases = await fetch_abandoned(client, now)
    print(f"Agendamentos abandonados a avaliar para nudge: {len(cases)}")
    if not cases:
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
        for case in cases:
            await _send_nudge(client, graph, case, now)
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
