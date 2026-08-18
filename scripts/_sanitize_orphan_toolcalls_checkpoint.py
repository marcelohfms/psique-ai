"""
Sanitiza o checkpoint de um thread envenenado por tool_call desbalanceado.

Modo de falha (caso Kimmy/Darleide, 5581999656460, muda de 20/07 a 18/08/2026):
uma AIMessage no MEIO do histórico ficou com um `invalid_tool_call`
(call_EFxGeLiobyES3DCJnDPCcDiS) que estourou o cap de completion_tokens antes de
fechar o JSON dos args. Nesta versão do langchain_openai o invalid_tool_call é
serializado como um `tool_call` normal na chamada da OpenAI — mas nenhuma
ToolMessage responde a ele. Resultado: TODO turno seguinte leva 400
("assistant message with 'tool_calls' must be followed by tool messages"),
o grafo grava __error__ e morre antes de a Eva responder.

A recuperação em app/main.py NÃO cobre isso: ela só inspeciona a ÚLTIMA
AIMessage, e aqui o órfão está enterrado no meio (a última AIMessage não tem
tool_calls). Por isso o thread NUNCA se auto-curou.

Este one-off espelha o guard de código (_strip_orphan_tool_calls em
app/graph/nodes.py): remove do checkpoint persistido, via RemoveMessage, toda
AIMessage cujos tool_calls/invalid_tool_calls não têm TODA resposta
correspondente — em vez de injetar uma ToolMessage no fim, que violaria a regra
posicional da OpenAI (a resposta precisa vir logo após o emissor). Com o guard
de código já em produção o thread se cura sozinho na próxima mensagem; este
script serve para limpar o estado persistido e reativar a Eva de imediato.

Uso:
    uv run python scripts/_sanitize_orphan_toolcalls_checkpoint.py            # dry-run
    uv run python scripts/_sanitize_orphan_toolcalls_checkpoint.py --apply    # aplica

Por padrão roda em dry-run (só mostra o que faria). Passe --apply para gravar.
"""
import asyncio
import sys
from dotenv import load_dotenv
load_dotenv()

# Thread(s) a sanitizar. Ajuste conforme necessário.
THREADS = ["5581999656460@s.whatsapp.net"]


def _emitted_ids(msg) -> list:
    ids = []
    for bucket in (
        getattr(msg, "tool_calls", None) or [],
        getattr(msg, "invalid_tool_calls", None) or [],
        (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or [],
    ):
        for tc in bucket:
            tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if tid:
                ids.append(tid)
    return ids


async def main(apply: bool):
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import RemoveMessage
    import psycopg
    import os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    conn = await psycopg.AsyncConnection.connect(conn_str, autocommit=True)
    conn.prepare_threshold = None  # evita reuso de prepared statement pelo pooler
    async with conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        for thread in THREADS:
            config = {"configurable": {"thread_id": thread}}
            print(f"\n===== thread={thread} =====")
            snapshot = await graph.aget_state(config)
            if not snapshot or not snapshot.values:
                print("  (sem estado)")
                continue
            msgs = snapshot.values.get("messages") or []

            responded = {
                getattr(m, "tool_call_id", None)
                for m in msgs
                if getattr(m, "type", None) == "tool"
            }
            responded.discard(None)

            to_remove = []
            for m in msgs:
                if getattr(m, "type", None) != "ai":
                    continue
                eids = _emitted_ids(m)
                if eids and not all(e in responded for e in eids):
                    to_remove.append(m)

            if not to_remove:
                print("  Nada para sanitizar (histórico balanceado).")
                continue

            for m in to_remove:
                print(f"  remover AIMessage id={m.id!r} tool_calls sem resposta="
                      f"{[e for e in _emitted_ids(m) if e not in responded]}")

            if not apply:
                print("  (dry-run — passe --apply para gravar)")
                continue

            missing_id = [m for m in to_remove if not getattr(m, "id", None)]
            if missing_id:
                print("  ABORTADO: alguma mensagem alvo não tem .id; "
                      "RemoveMessage precisa do id.")
                continue

            await graph.aupdate_state(
                config,
                {"messages": [RemoveMessage(id=m.id) for m in to_remove]},
                as_node="patient_agent",
            )
            print(f"  OK: {len(to_remove)} mensagem(ns) removida(s).")

            # Verificação pós-sanitização
            snap2 = await graph.aget_state(config)
            msgs2 = snap2.values.get("messages") or []
            resp2 = {getattr(m, "tool_call_id", None) for m in msgs2 if getattr(m, "type", None) == "tool"}
            resp2.discard(None)
            still_bad = [
                m for m in msgs2
                if getattr(m, "type", None) == "ai"
                and _emitted_ids(m) and not all(e in resp2 for e in _emitted_ids(m))
            ]
            print(f"  pós: {len(msgs2)} mensagens, next={snap2.next}, "
                  f"desbalanceadas restantes={len(still_bad)}")


if __name__ == "__main__":
    apply = "--apply" in sys.argv[1:]
    asyncio.run(main(apply))
