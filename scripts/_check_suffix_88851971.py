import asyncio
from dotenv import load_dotenv
load_dotenv()

SUFFIX = "88851971"


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    resp = await client.from_("contacts").select("*").ilike("phone", f"%{SUFFIX}%").execute()
    print(f"contatos com sufixo {SUFFIX}: {len(resp.data)}")
    for c in resp.data:
        print(c)

    for s in [SUFFIX[1:], SUFFIX[:-1]]:
        resp2 = await client.from_("contacts").select("*").ilike("phone", f"%{s}%").execute()
        print(f"\ncontatos com sufixo {s}: {len(resp2.data)}")
        for c in resp2.data:
            print(c)

asyncio.run(main())
