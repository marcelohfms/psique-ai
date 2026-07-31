import asyncio, os
from dotenv import load_dotenv
load_dotenv()
THREAD = "5581987415206@s.whatsapp.net"
KEYS = ["stage","user_name","patient_name","guardian_name","guardian_relationship","guardian_cpf",
        "patient_email","birth_date","patient_age","is_patient","is_returning_patient","preferred_doctor","_is_patient_confirmed"]
async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    conn = await AsyncConnection.connect(os.environ["SUPABASE_CONNECTION_STRING"],
        autocommit=True, prepare_threshold=None, row_factory=dict_row)
    async with conn:
        cp = AsyncPostgresSaver(conn)
        items = [c async for c in cp.alist({"configurable": {"thread_id": THREAD}})]
        items.reverse()
        prev = {}
        for c in items:
            v = c.checkpoint["channel_values"]
            cur = {k: v.get(k) for k in KEYS}
            diff = {k: (prev.get(k), cur[k]) for k in KEYS if prev.get(k) != cur[k]}
            if diff:
                print(c.checkpoint["ts"], diff)
            prev = cur
asyncio.run(main())
