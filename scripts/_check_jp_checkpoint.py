import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581991542212@s.whatsapp.net"  # João Pedro

async def main():
    import psycopg
    dsn = os.environ["SUPABASE_CONNECTION_STRING"]
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
        saver = AsyncPostgresSaver(conn)
        cfg = {"configurable": {"thread_id": PHONE}}
        cp = await saver.aget(cfg)
        if not cp:
            print("sem checkpoint"); return
        msgs = cp["channel_values"].get("messages", [])
        print(f"total msgs no checkpoint: {len(msgs)}")
        for m in msgs[-30:]:
            t = getattr(m, "type", "?")
            tcs = getattr(m, "tool_calls", None) or []
            content = str(getattr(m, "content", ""))[:120]
            if tcs:
                for tc in tcs:
                    print(f"  [{t}] TOOL_CALL {tc.get('name')} args={tc.get('args')}")
            elif t == "tool":
                print(f"  [tool:{getattr(m,'name','?')}] {content[:200]}")
            else:
                print(f"  [{t}] {content}")
        print("\npending_appointment no state:", cp["channel_values"].get("pending_appointment"))

asyncio.run(main())
