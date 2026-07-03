import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    for thread in ["5581996001122", "5581996001122@s.whatsapp.net"]:
        r = await client.from_("checkpoints").select("thread_id, checkpoint_id, metadata") \
            .eq("thread_id", thread).order("checkpoint_id", desc=True).limit(1).execute()
        if r.data:
            meta = r.data[0].get("metadata") or {}
            if isinstance(meta, str): meta = json.loads(meta)
            print(f"thread={thread} | step={meta.get('step')} | source={meta.get('source')}")

            # Lê o blob de channel_values para ver is_patient e user_name
            blobs = await client.from_("checkpoint_blobs").select("channel, type, blob") \
                .eq("thread_id", thread).eq("version", "1").execute()

            # Tenta pegar via checkpoint direto
            cp_data = r.data[0]
            checkpoint = cp_data.get("checkpoint") or {}
            if isinstance(checkpoint, str): checkpoint = json.loads(checkpoint)
            cv = checkpoint.get("channel_values", {})
            print(f"  is_patient={cv.get('is_patient')} | user_name={cv.get('user_name')} | patient_name={cv.get('patient_name')}")
        else:
            print(f"  ✗ {thread}: sem checkpoint")

asyncio.run(main())
