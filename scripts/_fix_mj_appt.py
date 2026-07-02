import asyncio
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "rj02m9ar2ffi1t181tluuk293k"
MARIA_JOSE_USER_ID = "1f0f1533-d31b-4d48-8bea-39d44f68898d"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    await client.from_("appointments").update({
        "user_id": MARIA_JOSE_USER_ID,
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Consulta transferida para Maria José Alves de Farias")

    # Confirma
    a = await client.from_("appointments").select("user_id, start_time, status") \
        .eq("appointment_id", APPOINTMENT_ID).single().execute()
    print(f"   user_id={a.data['user_id']} | status={a.data['status']}")

asyncio.run(main())
