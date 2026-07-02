import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone, _strip_phone

    phone = "5581999480798"
    client = await get_supabase()

    print("=== USERS (contacts/patients) ===")
    users = await get_users_by_phone(phone)
    for u in users:
        print(u)

    print("\n=== RECENT MESSAGES ===")
    msgs = await client.from_("messages").select("*").eq("phone", _strip_phone(phone)).order("created_at", desc=True).limit(40).execute()
    for m in reversed(msgs.data or []):
        print(f"{m.get('created_at')[:19]} | {m.get('role')}: {m.get('content')[:300]}")

    print("\n=== RECENT EVENTS ===")
    evs = await client.from_("events").select("*").eq("phone", _strip_phone(phone)).order("created_at", desc=True).limit(30).execute()
    for e in reversed(evs.data or []):
        print(f"{e.get('created_at')[:19]} | {e.get('event_type')} | {e.get('metadata')}")

asyncio.run(main())
