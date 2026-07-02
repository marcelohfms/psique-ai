import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    OLD_ID = "2ca01880-0c27-4305-bfb8-342c90065e08"
    NEW_ID = "1a878993-843c-4cfa-a6e5-9ec720b4dd89"
    GISLANNY_CONTACT_ID = "77b64d89-0e8e-43c0-a16a-72ef72103a22"

    # Move appointment to old patient
    res = await client.table("appointments").update({"patient_id": OLD_ID}).eq("patient_id", NEW_ID).execute()
    print("APPT MOVED:", res.data)

    # Re-point Gislanny's patient_contacts to old patient
    res2 = await client.table("patient_contacts").update({"patient_id": OLD_ID}).eq("patient_id", NEW_ID).eq("contact_id", GISLANNY_CONTACT_ID).execute()
    print("PC MOVED:", res2.data)

    # Delete duplicate patient
    res3 = await client.table("patients").delete().eq("id", NEW_ID).execute()
    print("DELETED DUP PATIENT:", res3.data)

asyncio.run(main())
