import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone, _phone_variants

    phone = "5581992158100@s.whatsapp.net"
    variants = _phone_variants(phone)
    print("Variantes buscadas:", variants)

    users = await get_users_by_phone(phone)
    print(f"\n=== {len(users) if users else 0} registro(s) encontrado(s) ===")
    if not users:
        client = await get_supabase()
        for v in variants:
            r = await client.from_("contacts").select("*").eq("phone", v).execute()
            print(f"contacts.phone={v} -> {r.data}")
        return

    for u in users:
        print("\n--- registro ---")
        for k, v in u.items():
            print(f"  {k}: {v}")

asyncio.run(main())
