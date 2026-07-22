"""
One-off: completa/corrige o cadastro do paciente Victor Silva do Nascimento Franca
(5581995106914, contato Maria José da Silva, mãe).

Correções:
- email (informado no chat às 03/07 09:36: mmariajose11291@gmail.com)
- guardian_relationship (Maria José é mãe do Victor)
- is_returning_patient: estava True incorretamente — é a primeira consulta (novo
  paciente), não retornante. Corrigido para False.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581995106914@s.whatsapp.net"


async def main():
    from app.database import upsert_user, get_users_by_phone, is_registration_complete

    await upsert_user(PHONE, {
        "email": "mmariajose11291@gmail.com",
        "guardian_relationship": "mãe",
        "is_patient": False,
        "is_returning_patient": False,
    })
    print("✅ Cadastro atualizado.")

    users = await get_users_by_phone(PHONE)
    u = users[0]
    print(f"email={u.get('email')} | guardian_relationship={u.get('guardian_relationship')}")
    print(f"is_returning_patient={u.get('is_returning_patient')} | guardian_cpf={u.get('guardian_cpf')}")
    print(f"is_registration_complete = {is_registration_complete(u)}")


asyncio.run(main())
