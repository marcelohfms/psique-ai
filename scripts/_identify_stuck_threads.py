import asyncio
from dotenv import load_dotenv
load_dotenv()

NUMBERS = [
    "558179087152",
    "558186651067",
    "558191183875",
    "5581992717880",
    "558199687066",
]


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for n in NUMBERS:
        c = await client.from_("contacts").select("name,phone").eq("phone", n).execute()
        if not c.data:
            # try legacy/canonical variant
            from app.database import _phone_variants
            for v in _phone_variants(n):
                c = await client.from_("contacts").select("name,phone").eq("phone", v).execute()
                if c.data:
                    break
        print(n, "->", c.data)


asyncio.run(main())
