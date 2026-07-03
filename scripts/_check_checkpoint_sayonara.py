import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Tenta TODAS as variantes de thread_id
    threads = [
        "558195302944",
        "558195302944@s.whatsapp.net",
        "5581995302944",
        "5581995302944@s.whatsapp.net",
    ]
    for t in threads:
        r = await client.from_("checkpoints").select("thread_id, checkpoint_id, thread_ts, metadata") \
            .eq("thread_id", t).order("checkpoint_id", desc=True).limit(3).execute()
        if r.data:
            print(f"\n✅ CHECKPOINT ENCONTRADO — thread_id={t}")
            for cp in r.data:
                meta = cp.get("metadata") or {}
                if isinstance(meta, str): meta = json.loads(meta)
                print(f"  checkpoint_id={cp['checkpoint_id']} | thread_ts={cp.get('thread_ts')}")
                print(f"  metadata: {meta}")
        else:
            print(f"  ✗ {t}: sem checkpoint")

    # Também verifica erros no log
    print("\n--- Mensagens com roles reais ---")
    msgs = await client.from_("messages").select("role, content, created_at") \
        .eq("phone", "558195302944").order("created_at").execute()
    for m in msgs.data:
        ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        content = (m["content"] or "")[:120].replace("\n", " ")
        print(f"  {ts} [{m['role']:9}] {content}")

asyncio.run(main())
