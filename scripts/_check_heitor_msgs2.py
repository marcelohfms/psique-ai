import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    variants = _phone_variants("5581996001122")
    print(f"Variantes: {variants}")

    for v in variants + ["558196001122"]:
        msgs = await client.from_("messages").select("phone, role, content, created_at") \
            .eq("phone", v).order("created_at", desc=True).limit(10).execute()
        if msgs.data:
            print(f"\n=== {v} ({len(msgs.data)} msgs) ===")
            for m in reversed(msgs.data):
                ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
                content = (m["content"] or "")[:200].replace("\n", " ")
                print(f"  {ts} [{m['role']:9}] {content}")
        else:
            print(f"  {v}: sem mensagens")

    # Também verifica eventos
    evts = await client.from_("events").select("phone, event_type, created_at") \
        .ilike("phone", "%996001122%").order("created_at", desc=True).limit(5).execute()
    print(f"\nEventos: {[e['phone'] + ' | ' + e['event_type'] for e in evts.data]}")

asyncio.run(main())
