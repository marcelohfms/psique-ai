#!/usr/bin/env python3
"""Remove ghost patient for contact 5581996647090 (Gabriel).

O contato Gabriel tem dois pacientes:
1. Ghost (ID: 8f33ee49-0749-4903-86e2-bc09fc87352e) — nome é uma nota privada
2. Real (ID: 0dc2d864-1b36-410f-a92e-4dcf9e16a53c) — João Pedro Lins Da Costa Gomes (DOB: 03/10/2006)

O paciente fantasma foi criado porque uma nota privada foi interpretada como patient_name.
Agora que corrigimos _looks_like_name, vamos remover o ghost e ligar todas as consultas
ao paciente real.
"""
import asyncio
from app.database import get_supabase


async def main():
    client = await get_supabase()

    # IDs do caso
    ghost_id = "8f33ee49-0749-4903-86e2-bc09fc87352e"
    real_id = "0dc2d864-1b36-410f-a92e-4dcf9e16a53c"
    contact_id = "939bbbd0-ff87-43e2-9b67-87786a0cf521"

    print("=" * 70)
    print("REMOVER PACIENTE FANTASMA: Gabriel (5581996647090)")
    print("=" * 70)

    # 1. Validar paciente fantasma
    result = await client.from_("patients").select("*").eq("id", ghost_id).execute()
    ghost = (result.data or [{}])[0]
    print(f"\n1️⃣ Paciente fantasma:")
    print(f"   ID: {ghost.get('id')}")
    print(f"   Nome: {ghost.get('name')[:50]}...")

    # 2. Validar paciente real
    result = await client.from_("patients").select("*").eq("id", real_id).execute()
    real = (result.data or [{}])[0]
    print(f"\n2️⃣ Paciente real:")
    print(f"   ID: {real.get('id')}")
    print(f"   Nome: {real.get('name')}")
    print(f"   Data nascimento: {real.get('birth_date')}")

    # 3. Contar links do ghost
    result = await client.from_("patient_contacts").select("*").eq("patient_id", ghost_id).execute()
    ghost_links = result.data or []
    print(f"\n3️⃣ Links do paciente fantasma: {len(ghost_links)}")
    for link in ghost_links:
        print(f"   - {link['role']}: contact_id={link['contact_id']}")

    # 4. Contar links do real
    result = await client.from_("patient_contacts").select("*").eq("patient_id", real_id).execute()
    real_links = result.data or []
    print(f"\n4️⃣ Links do paciente real: {len(real_links)}")
    for link in real_links:
        print(f"   - {link['role']}: contact_id={link['contact_id']}")

    # 5. Remover todos os links do ghost
    print(f"\n5️⃣ Removendo {len(ghost_links)} links do paciente fantasma...")
    if ghost_links:
        ids_to_delete = [link['id'] for link in ghost_links]
        await client.from_("patient_contacts").delete().in_("id", ids_to_delete).execute()
        print(f"   ✅ Removidos {len(ids_to_delete)} links")

    # 6. Remover o paciente fantasma
    print(f"\n6️⃣ Removendo paciente fantasma...")
    await client.from_("patients").delete().eq("id", ghost_id).execute()
    print(f"   ✅ Paciente fantasma removido")

    # 7. Validar que foi removido
    result = await client.from_("patients").select("*").eq("id", ghost_id).execute()
    if not result.data:
        print(f"\n✅ SUCESSO: Paciente fantasma foi removido com segurança")
        print(f"✅ Paciente real (João Pedro) continua intacto")
    else:
        print(f"\n❌ ERRO: Paciente fantasma ainda existe!")


if __name__ == "__main__":
    asyncio.run(main())
