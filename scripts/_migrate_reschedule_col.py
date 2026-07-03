import asyncio, os
from dotenv import load_dotenv
load_dotenv()

async def main():
    conn_str = os.environ.get("SUPABASE_CONNECTION_STRING")
    if not conn_str:
        print("SUPABASE_CONNECTION_STRING não definida")
        return
    from psycopg import AsyncConnection
    conn = await AsyncConnection.connect(conn_str, autocommit=True)
    await conn.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reschedule_requested_at timestamptz;")
    await conn.close()
    print("✅ Coluna reschedule_requested_at criada")

asyncio.run(main())
