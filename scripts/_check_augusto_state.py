import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone
    client = await get_supabase()

    # Estado no banco
    users = await get_users_by_phone("5581991827596@s.whatsapp.net")
    for u in users:
        print(f"DB: name={u.get('name')} | patient_name={u.get('patient_name')} | is_patient={u.get('is_patient')}")

    # Checkpoint
    for thread in ["5581991827596@s.whatsapp.net", "558191827596@s.whatsapp.net"]:
        r = await client.from_("checkpoints").select("thread_id, checkpoint_id, checkpoint") \
            .eq("thread_id", thread).order("checkpoint_id", desc=True).limit(1).execute()
        if r.data:
            cp = r.data[0]
            data = cp.get("checkpoint") or {}
            if isinstance(data, str): data = json.loads(data)
            print(f"\nCheckpoint thread_id={thread}")
            # Busca channel values nos blobs
            blobs = await client.from_("checkpoint_blobs").select("channel, type, blob") \
                .eq("thread_id", thread).eq("checkpoint_id", cp["checkpoint_id"]) \
                .in_("channel", ["is_patient", "user_name", "patient_name", "stage"]).execute()
            for b in blobs.data:
                print(f"  {b['channel']}: type={b['type']} blob={str(b.get('blob',''))[:80]}")

asyncio.run(main())
