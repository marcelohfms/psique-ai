"""Corrige patient_contacts.is_self=False incorreto em role="consulta" para
casos com o mesmo padrão da Daniela De Souza Passos (patient_id=9be9c778-fba9-4f2a-95ea-0633902c26af).

Casos corrigidos:
- Paula Muniz Evangelista, patient_id=b3c58f9c-a8f3-41a5-8440-ca9bfab9bafc, telefone 5581985580824
- Juliana Sampaio Barbosa Tenorio Vilaça, patient_id=b3970893-6111-471a-8392-75233c0ccfa1, telefone 5581991897010

Ação: UPDATE patient_contacts SET is_self=True WHERE patient_id=... AND role='consulta'
AND contact_id = (contato cujo telefone é o informado acima).
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from app.supabase_client import get_supabase

CASES = [
    {
        "patient_id": "b3c58f9c-a8f3-41a5-8440-ca9bfab9bafc",
        "name": "Paula Muniz Evangelista",
        "phone": "5581985580824",
    },
    {
        "patient_id": "b3970893-6111-471a-8392-75233c0ccfa1",
        "name": "Juliana Sampaio Barbosa Tenorio Vilaça",
        "phone": "5581991897010",
    },
]


async def main():
    client = await get_supabase()

    for case in CASES:
        pid = case["patient_id"]
        phone = case["phone"]

        contact_res = await client.from_("contacts").select("id,phone").eq("phone", phone).execute()
        contacts = contact_res.data or []
        if not contacts:
            print(f"[{case['name']}] ERRO: nenhum contato encontrado com telefone {phone}")
            continue
        if len(contacts) > 1:
            print(f"[{case['name']}] ERRO: múltiplos contatos com telefone {phone}: {contacts}")
            continue
        contact_id = contacts[0]["id"]

        pc_res = (
            await client.from_("patient_contacts")
            .select("patient_id,contact_id,role,is_self")
            .eq("patient_id", pid)
            .eq("contact_id", contact_id)
            .eq("role", "consulta")
            .execute()
        )
        rows = pc_res.data or []
        if not rows:
            print(f"[{case['name']}] ERRO: nenhuma linha patient_contacts (patient_id={pid}, contact_id={contact_id}, role=consulta)")
            continue
        if len(rows) > 1:
            print(f"[{case['name']}] ERRO: múltiplas linhas encontradas: {rows}")
            continue

        row = rows[0]
        if row["is_self"] is True:
            print(f"[{case['name']}] já está is_self=True, nada a fazer.")
            continue

        update_res = (
            await client.from_("patient_contacts")
            .update({"is_self": True})
            .eq("patient_id", pid)
            .eq("contact_id", contact_id)
            .eq("role", "consulta")
            .execute()
        )
        print(f"[{case['name']}] atualizado: {update_res.data}")


asyncio.run(main())
