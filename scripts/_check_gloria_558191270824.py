import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONES = ["558191270824", "5581991270824"]

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    print("=== CONTACTS ===")
    for p in PHONES:
        c = await client.table("contacts").select("*").eq("phone", p).execute()
        for row in c.data:
            print(p, "->", {k: row.get(k) for k in ("id","name","phone","active","is_self")})

    print("\n=== MESSAGES ===")
    for p in PHONES:
        msgs = await client.table("messages").select("*").eq("phone", p).order("created_at", desc=False).execute()
        if msgs.data:
            print(f"--- phone {p}: {len(msgs.data)} msgs ---")
            for m in msgs.data[-50:]:
                print(m.get("created_at"), "|", m.get("role"), "|", (m.get("content") or "")[:300])

asyncio.run(main())
