import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import json
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    PHONE = "5581995302944"

    # Busca o checkpoint com thread_id = phone
    r = await client.from_("checkpoints").select("*").eq("thread_id", PHONE).order("checkpoint_id", desc=True).limit(3).execute()
    if not r.data:
        print("Nenhum checkpoint encontrado")
        return

    for cp in r.data:
        print(f"checkpoint_id={cp.get('checkpoint_id')} | thread_ts={cp.get('thread_ts')}")
        # O state fica em 'checkpoint' (JSONB)
        checkpoint_data = cp.get("checkpoint") or {}
        if isinstance(checkpoint_data, str):
            checkpoint_data = json.loads(checkpoint_data)
        channel_values = checkpoint_data.get("channel_values", {})
        messages = channel_values.get("messages", [])
        print(f"  {len(messages)} mensagens no estado")
        for m in messages[-20:]:
            role = m.get("type") or m.get("id", ["?"])[-1]
            content = str(m.get("content") or "")[:200].replace("\n", " ")
            print(f"  [{role}] {content}")

asyncio.run(main())
