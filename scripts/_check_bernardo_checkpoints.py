import asyncio, os
from dotenv import load_dotenv
load_dotenv()

THREAD = "5581987415206@s.whatsapp.net"

async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    conn = await AsyncConnection.connect(
        os.environ["SUPABASE_CONNECTION_STRING"],
        autocommit=True, prepare_threshold=None, row_factory=dict_row,
    )
    async with conn:
        cp = AsyncPostgresSaver(conn)
        items = []
        async for c in cp.alist({"configurable": {"thread_id": THREAD}}):
            items.append(c)
        items.reverse()
        print("total", len(items))
        prev = 0
        for c in items:
            ch = c.checkpoint
            vals = ch.get("channel_values", {})
            msgs = vals.get("messages", []) or []
            print(f"--- {ch['ts']} stage={vals.get('stage')} nmsg={len(msgs)}")
            for m in msgs[prev:]:
                tc = getattr(m, "tool_calls", None)
                extra = f" TOOLCALLS={[t['name'] for t in tc]}" if tc else ""
                print(f"    {type(m).__name__}: {str(getattr(m,'content',''))[:200]!r}{extra}")
            prev = len(msgs)
        if items:
            v = items[-1].checkpoint["channel_values"]
            print("\nFINAL STATE (non-message keys):")
            for k, val in v.items():
                if k != "messages":
                    print(f"  {k} = {val!r}")

asyncio.run(main())
