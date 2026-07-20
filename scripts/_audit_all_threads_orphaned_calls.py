import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg, os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    conn = await psycopg.AsyncConnection.connect(conn_str, autocommit=True)
    conn.prepare_threshold = None
    async with conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
                ("%@s.whatsapp.net",),
            )
            threads = [r[0] for r in await cur.fetchall()]

        print(f"escaneando {len(threads)} threads...")
        stuck = []
        for i, thread in enumerate(threads):
            config = {"configurable": {"thread_id": thread}}
            try:
                snapshot = await graph.aget_state(config)
            except Exception as e:
                print(f"  erro ao ler {thread}: {e}")
                continue
            if not snapshot or not snapshot.values:
                continue
            msgs = snapshot.values.get("messages") or []
            if not msgs:
                continue
            last_ai = next((m for m in reversed(msgs) if getattr(m, "type", None) == "ai"), None)
            if not last_ai:
                continue
            pending = (getattr(last_ai, "tool_calls", []) or []) + (getattr(last_ai, "invalid_tool_calls", []) or [])
            if not pending:
                continue
            answered_ids = {getattr(m, "tool_call_id", None) for m in msgs if getattr(m, "type", None) == "tool"}
            orphaned = [c for c in pending if c["id"] not in answered_ids]
            if orphaned:
                # count trailing unanswered human messages piled up after the break
                trailing_human = 0
                for m in reversed(msgs):
                    if getattr(m, "type", None) == "human":
                        trailing_human += 1
                    else:
                        break
                stuck.append((thread, len(orphaned), trailing_human))

        print(f"\n{len(stuck)} threads travados com tool_call orfão AGORA:")
        for t, n_orphan, n_trailing in stuck:
            print(f"  {t}  orphaned_calls={n_orphan}  mensagens_do_paciente_sem_resposta={n_trailing}")


asyncio.run(main())
