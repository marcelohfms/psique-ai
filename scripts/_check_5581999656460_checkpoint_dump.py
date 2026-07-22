import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg
    import os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        for thread in ["5581999656460@s.whatsapp.net", "558199656460@s.whatsapp.net"]:
            config = {"configurable": {"thread_id": thread}}
            print(f"\n\n========== thread={thread} ==========")
            snapshot = await graph.aget_state(config)
            if not snapshot or not snapshot.values:
                print("  (no state)")
                continue
            print("  stage:", snapshot.values.get("stage"))
            print("  next:", snapshot.next)
            msgs = snapshot.values.get("messages") or []
            print(f"  {len(msgs)} messages in state:")
            for m in msgs:
                mtype = getattr(m, "type", "?")
                content = getattr(m, "content", "")
                tool_calls = getattr(m, "tool_calls", None)
                print(f"    [{mtype}] {str(content)[:300]}")
                if tool_calls:
                    print(f"        tool_calls: {tool_calls}")

            # history of checkpoints (state transitions) with tasks/errors
            print("\n  --- history ---")
            async for hist in graph.aget_state_history(config):
                print(f"  step -> next={hist.next} tasks={[ (t.name, getattr(t, 'error', None)) for t in hist.tasks]}")

asyncio.run(main())
