import asyncio
from dotenv import load_dotenv
load_dotenv()

# Duplicate patients for contact 5581999784308 (Carla Spinelli Ferrari Arruda).
# Survivor: 48cf5971 (older, created 2026-06-23, has legacy_user_id link).
# Duplicate: e413609a (created 2026-06-30, has patient_cpf that survivor lacks).
# Neither has appointments, so no reassignment needed there.
SURVIVOR = "48cf5971-5996-4a93-8337-bf8d8932b8ab"
DUPLICATE = "e413609a-1240-4eb1-8bb2-ea3216275ebb"

async def main():
    from app.database import get_supabase

    client = await get_supabase()

    # 1. Copy CPF from duplicate into survivor (survivor lacks it).
    dup = await client.from_("patients").select("patient_cpf").eq("id", DUPLICATE).execute()
    cpf = dup.data[0]["patient_cpf"]
    if cpf:
        await client.from_("patients").update({"patient_cpf": cpf}).eq("id", SURVIVOR).execute()
        print(f"Copied patient_cpf={cpf} to survivor {SURVIVOR}")

    # 2. Safety check: no appointments should reference the duplicate.
    appts = await client.from_("appointments").select("appointment_id").eq("patient_id", DUPLICATE).execute()
    if appts.data:
        raise RuntimeError(f"Duplicate {DUPLICATE} still has appointments, aborting: {appts.data}")

    # 3. Delete duplicate's patient_contacts links.
    pc_del = await client.from_("patient_contacts").delete().eq("patient_id", DUPLICATE).execute()
    print(f"Deleted {len(pc_del.data)} patient_contacts rows for duplicate")

    # 4. Delete duplicate patient row.
    p_del = await client.from_("patients").delete().eq("id", DUPLICATE).execute()
    print(f"Deleted patient row: {p_del.data}")

    # 5. Verify survivor state.
    survivor = await client.from_("patients").select("*").eq("id", SURVIVOR).execute()
    print("=== SURVIVOR (final) ===")
    print(survivor.data)

asyncio.run(main())
