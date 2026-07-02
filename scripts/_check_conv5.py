import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Lista todas as tabelas disponíveis
    tables = await client.from_("information_schema.tables") \
        .select("table_name") \
        .eq("table_schema", "public") \
        .execute()
    print("Tabelas:", [t["table_name"] for t in tables.data])

asyncio.run(main())
