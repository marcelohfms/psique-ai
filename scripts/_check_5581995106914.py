import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_users_by_phone, is_registration_complete, DOCTOR_NAMES

    for phone in ["5581995106914@s.whatsapp.net", "558195106914@s.whatsapp.net"]:
        users = await get_users_by_phone(phone)
        print(f"=== {phone}: {len(users) if users else 0} user(s) ===")
        if not users:
            continue
        for u in users:
            print(f"id={u['id']}")
            for k, v in u.items():
                print(f"  {k}: {v!r}")
            complete = is_registration_complete(u)
            print(f"\n  is_registration_complete = {complete}")
            if not complete:
                required = ["name", "email", "birth_date", "doctor_id"]
                missing = [f for f in required if not u.get(f)]
                if u.get("is_patient") is None:
                    missing.append("is_patient")
                age = u.get("age")
                if age is not None and age < 18:
                    if u.get("doctor_id") == DOCTOR_NAMES and False:
                        pass
                    for f in ["guardian_name", "guardian_relationship"]:
                        if not u.get(f):
                            missing.append(f)
                    if u.get("is_returning_patient") is False and not u.get("guardian_cpf"):
                        missing.append("guardian_cpf")
                if u.get("is_patient") is False and not u.get("patient_name"):
                    missing.append("patient_name")
                print(f"  campos faltando (aprox): {missing}")

asyncio.run(main())
