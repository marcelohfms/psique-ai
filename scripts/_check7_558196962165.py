import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "j74rkero61h40fjm0794gska2o"
SUZI = "3b0a5557-bef2-4c22-b416-5f564deb7592"
LAILA = "ae408243-53d1-4b62-88a5-f323a6d710df"

async def main():
    from app.database import get_supabase
    client = await get_supabase()

    print("=== FULL APPOINTMENT ROW ===")
    a = await client.table("appointments").select("*").eq("appointment_id", APPT).execute()
    for row in a.data:
        for k,v in row.items():
            print(f"  {k}: {v}")

    print("\n=== PAYMENTS by appointment_id ===")
    p = await client.table("payments").select("*").eq("appointment_id", APPT).execute()
    for row in p.data:
        print(row)

    print("\n=== ALL PAYMENTS for SUZI ===")
    p2 = await client.table("payments").select("*").eq("patient_id", SUZI).execute()
    for row in p2.data:
        print(row)

asyncio.run(main())
