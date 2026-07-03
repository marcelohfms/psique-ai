import asyncio

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    users = await client.from_("users").select("id, name, number").execute()
    found = []
    for u in users.data:
        num = u.get("number", "") or ""
        if len(num) > 13:
            found.append(f"  {u['name']} | {num} | len={len(num)}")
    if found:
        for f in found:
            print(f)
    else:
        print("Nenhum número com mais de 13 dígitos.")

asyncio.run(main())
