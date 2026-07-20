import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONES = ["5581995106914", "5581995106914@s.whatsapp.net"]

async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph.graph import build_graph

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    pg_conn = await AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row
    )
    checkpointer = AsyncPostgresSaver(pg_conn)
    graph = build_graph(checkpointer=checkpointer)

    for PHONE in PHONES:
        config = {"configurable": {"thread_id": PHONE}}
        snap = await graph.aget_state(config)
        print(f"\n=== thread_id={PHONE} ===")
        if not snap or not snap.values:
            print("Nenhum checkpoint encontrado.")
            continue
        for k, v in snap.values.items():
            if k == "messages":
                continue
            print(f"{k}: {v!r}")

    await pg_conn.close()

asyncio.run(main())
