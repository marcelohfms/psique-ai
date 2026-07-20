import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    patterns = ["%ANDREANA%", "%ANDREA%", "%BESERRA%", "%QUITUTE%", "%QUIDUTE%SOLE%", "%SOLE%"]
    for p in patterns:
        res = await client.table("patients").select("id,name,birth_date,email,doctor_id,created_at").ilike("name", p).execute()
        print(f"patients ilike {p}:", res.data)

    for p in patterns:
        res = await client.table("contacts").select("id,name,phone,created_at").ilike("name", p).execute()
        print(f"contacts ilike {p}:", res.data)

    # legacy users table, if it still exists
    try:
        res = await client.table("users").select("*").ilike("name", "%ANDREANA%").execute()
        print("legacy users ANDREANA:", res.data)
    except Exception as e:
        print("users table check failed:", e)

asyncio.run(main())
