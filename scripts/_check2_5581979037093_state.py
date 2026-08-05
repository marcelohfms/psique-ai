import asyncio, os
from dotenv import load_dotenv
load_dotenv()
PHONE = "5581979037093"

async def main():
    import psycopg
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph.graph import build_graph
    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row
    ) as conn:
        graph = build_graph(checkpointer=AsyncPostgresSaver(conn))
        tid = f"{PHONE}@s.whatsapp.net"
        snap = await graph.aget_state({"configurable": {"thread_id": tid, "phone": tid}})
        v = snap.values or {}
        print("NEXT:", snap.next)
        print("KEYS:", {k: (str(val)[:120]) for k, val in v.items() if k != "messages"})
        for i, m in enumerate(v.get("messages") or []):
            t = getattr(m, "type", "?")
            c = str(getattr(m, "content", ""))[:300].replace("\n", " | ")
            tc = getattr(m, "tool_calls", None)
            print(f"\n{i:3} [{t}] {c}")
            if tc:
                print("      TOOLCALLS:", [(x['name'], str(x['args'])[:200]) for x in tc])
            if getattr(m, "name", None):
                print("      name:", m.name)
            am = getattr(m, "additional_kwargs", None)
            if am:
                print("      addl:", str(am)[:200])

asyncio.run(main())
