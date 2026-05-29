"""
One-off: Mark the 2nd split-session appointment of Bruno Mota de Menezes as paid.

Root cause: when the 2nd slot was booked, the 1st was already "completed", so
confirm_appointment tagged it as "acompanhamento" instead of "primeira_consulta".
register_payment's linked-appointment query only fetches "primeira_consulta" rows,
so the 2nd appointment was never updated with paid_at.

Fix: copy paid_at and booking_fee_paid_at from the first paid appointment to all
unpaid appointments for this patient.
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()


async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    # Find Bruno's user record
    res = await client.from_("users").select("id, patient_name, name").ilike("patient_name", "%Bruno%Mota%").execute()
    if not res.data:
        res = await client.from_("users").select("id, patient_name, name").ilike("patient_name", "%Bruno%Menezes%").execute()

    if not res.data:
        print("Usuário não encontrado. Buscando por nome...")
        res = await client.from_("users").select("id, patient_name, name").ilike("patient_name", "%Bruno%").execute()
        for u in (res.data or []):
            print(f"  id={u['id']} patient_name={u['patient_name']} name={u['name']}")
        return

    user = res.data[0]
    print(f"Usuário encontrado: id={user['id']} patient_name={user['patient_name']}")

    # Fetch all appointments for this user
    appts_res = await client.from_("appointments").select(
        "appointment_id, start_time, status, consultation_type, paid_at, booking_fee_paid_at"
    ).eq("user_id", user["id"]).order("start_time").execute()

    appts = appts_res.data or []
    print(f"\nConsultas encontradas ({len(appts)}):")
    for a in appts:
        print(f"  {a['start_time'][:16]}  status={a['status']}  type={a['consultation_type']}  "
              f"paid_at={a['paid_at']}  booking_fee={a['booking_fee_paid_at']}")

    # Find the paid appointment
    paid_appts = [a for a in appts if a.get("paid_at")]
    unpaid_appts = [a for a in appts if not a.get("paid_at") and a["status"] in ("scheduled", "completed")]

    if not paid_appts:
        print("\nNenhuma consulta paga encontrada.")
        return

    if not unpaid_appts:
        print("\nNão há consultas pendentes de pagamento. Nada a fazer.")
        return

    paid_at_value = paid_appts[0]["paid_at"]
    booking_fee_value = paid_appts[0].get("booking_fee_paid_at") or paid_at_value

    print(f"\nVai copiar paid_at={paid_at_value} para {len(unpaid_appts)} consulta(s) pendente(s).")
    confirm = input("Confirmar? (s/N): ").strip().lower()
    if confirm != "s":
        print("Cancelado.")
        return

    for a in unpaid_appts:
        await client.from_("appointments").update({
            "paid_at": paid_at_value,
            "booking_fee_paid_at": booking_fee_value,
        }).eq("appointment_id", a["appointment_id"]).execute()
        print(f"  Atualizado: {a['appointment_id']} ({a['start_time'][:16]})")

    print("\nConcluído. ✅")


if __name__ == "__main__":
    asyncio.run(main())
