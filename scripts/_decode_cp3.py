import asyncio, json
from dotenv import load_dotenv
load_dotenv()

THREAD_ID = "558195302944@s.whatsapp.net"
LAST_CP_ID = "1f1658fc-46d9-6315-8011-9930dd7fa6e1"  # step 17

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Colunas de checkpoint_blobs e checkpoint_writes
    b = await client.from_("checkpoint_blobs").select("*").limit(1).execute()
    w = await client.from_("checkpoint_writes").select("*").limit(1).execute()
    print("checkpoint_blobs cols:", list(b.data[0].keys()) if b.data else "vazio")
    print("checkpoint_writes cols:", list(w.data[0].keys()) if w.data else "vazio")

    # Todos os writes associados ao thread (independente de checkpoint_id)
    writes = await client.from_("checkpoint_writes").select("*") \
        .eq("thread_id", THREAD_ID).execute()
    print(f"\nTotal writes para thread: {len(writes.data)}")
    for wr in writes.data[-10:]:
        channel = wr.get("channel") or wr.get("task_id") or ""
        blob = wr.get("blob") or ""
        blob_str = blob[:300] if isinstance(blob, str) else str(blob)[:300]
        print(f"  channel={channel} | {blob_str}")

asyncio.run(main())
