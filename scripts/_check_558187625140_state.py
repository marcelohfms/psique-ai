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

        for thread in ["5581987625140@s.whatsapp.net", "558187625140@s.whatsapp.net"]:
            config = {"configurable": {"thread_id": thread}}
            print(f"\n===== thread={thread} =====")
            snapshot = await graph.aget_state(config)
            if not snapshot or not snapshot.values:
                print("  (no state)")
                continue
            v = snapshot.values
            print("  stage:", v.get("stage"), " next:", snapshot.next)
            for k in ["user_name","patient_name","patient_age","is_patient","is_returning_patient",
                      "patient_cpf","guardian_cpf","doctor_id","birth_date","email"]:
                print(f"   {k}: {v.get(k)!r}")

asyncio.run(main())
