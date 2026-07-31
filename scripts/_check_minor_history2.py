import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for pattern in ["%menos de 18 anos%", "%primeira consulta%dividida%", "%duas sessões%", "%1 hora com os pais%", "%hora com os responsáveis%"]:
        res = await client.from_("messages").select("phone,created_at,content").ilike("content", pattern).order("created_at", desc=True).limit(5).execute()
        print(f"pattern={pattern} -> {len(res.data)} matches")
        for r in res.data:
            print("  ", r["created_at"], r["phone"], r["content"][:150])

asyncio.run(main())
