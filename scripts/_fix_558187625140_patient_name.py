import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    import psycopg
    import os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True, prepare_threshold=None) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)

        thread = "5581987625140@s.whatsapp.net"
        config = {"configurable": {"thread_id": thread}}

        before = await graph.aget_state(config)
        print("BEFORE patient_name:", before.values.get("patient_name"),
              "patient_age:", before.values.get("patient_age"),
              "next:", before.next)

        await graph.aupdate_state(config, {
            "patient_name": "Arthur Augusto Almeida dos Santos",
            "patient_age": 22,
        })

        after = await graph.aget_state(config)
        print("AFTER  patient_name:", after.values.get("patient_name"),
              "patient_age:", after.values.get("patient_age"),
              "user_name:", after.values.get("user_name"),
              "next:", after.next)

asyncio.run(main())
