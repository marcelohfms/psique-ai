import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    PHONE = "5581995302944"

    # Tenta com @s.whatsapp.net
    for thread in [PHONE, f"{PHONE}@s.whatsapp.net"]:
        r = await client.from_("checkpoints").select("thread_id, checkpoint_id, metadata") \
            .eq("thread_id", thread).order("checkpoint_id", desc=True).limit(1).execute()
        if r.data:
            print(f"Encontrado com thread_id={thread}")
            cp = r.data[0]
            meta = cp.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            print(f"  metadata keys: {list(meta.keys())}")
            
            # Busca o blob com o state
            blobs = await client.from_("checkpoint_blobs").select("channel, type, blob") \
                .eq("thread_id", thread).eq("checkpoint_id", cp["checkpoint_id"]).execute()
            for b in blobs.data:
                if b["channel"] == "messages" and b["type"] == "msgpack":
                    print(f"  messages blob encontrado ({len(b['blob'])} bytes)")
        else:
            print(f"Sem checkpoint para thread_id={thread}")

asyncio.run(main())
