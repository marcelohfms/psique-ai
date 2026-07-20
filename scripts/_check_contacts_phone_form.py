import asyncio
from dotenv import load_dotenv
load_dotenv()

SAMPLE_LEGACY = [
    "558199687066", "558179087152", "558196937559", "558181033571",
    "558196332827", "558196566872", "558181118614", "558188584125",
]


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    for legacy in SAMPLE_LEGACY:
        canon = legacy[:4] + "9" + legacy[4:]
        c = await client.from_("contacts").select("phone,name").in_("phone", [legacy, canon]).execute()
        print(legacy, "->", c.data)


asyncio.run(main())
