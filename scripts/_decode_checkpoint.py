import asyncio, json, struct
from dotenv import load_dotenv
load_dotenv()

THREAD_ID = "558195302944@s.whatsapp.net"
LAST_CP_ID = "1f1658eb-d78c-6a70-8001-7c6a0eb78dfb"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # metadata do checkpoint
    r = await client.from_("checkpoints").select("checkpoint_id, metadata, checkpoint") \
        .eq("thread_id", THREAD_ID).order("checkpoint_id", desc=True).limit(3).execute()
    for cp in r.data:
        meta = cp.get("metadata") or {}
        if isinstance(meta, str): meta = json.loads(meta)
        cp_data = cp.get("checkpoint") or {}
        if isinstance(cp_data, str): cp_data = json.loads(cp_data)
        print(f"\ncheckpoint_id={cp['checkpoint_id']}")
        print(f"  metadata: {meta}")
        channel_versions = cp_data.get("channel_versions", {})
        print(f"  channel_versions keys: {list(channel_versions.keys())}")
        
    # blobs do último checkpoint
    blobs = await client.from_("checkpoint_blobs").select("channel, type, blob") \
        .eq("thread_id", THREAD_ID).eq("checkpoint_id", LAST_CP_ID).execute()
    print(f"\nBlobs para último checkpoint ({len(blobs.data)}):")
    for b in blobs.data:
        print(f"  channel={b['channel']} type={b['type']} blob_len={len(b['blob']) if b['blob'] else 0}")

    # checkpoint_writes — tool calls e erros
    writes = await client.from_("checkpoint_writes").select("task_id, channel, type, blob") \
        .eq("thread_id", THREAD_ID).eq("checkpoint_id", LAST_CP_ID).execute()
    print(f"\nWrites para último checkpoint ({len(writes.data)}):")
    for w in writes.data:
        blob = w.get("blob")
        print(f"  channel={w['channel']} type={w['type']} blob_len={len(blob) if blob else 0}")
        if w["type"] == "json" and blob:
            try:
                data = json.loads(blob) if isinstance(blob, str) else blob
                print(f"    data: {str(data)[:300]}")
            except:
                pass

asyncio.run(main())
