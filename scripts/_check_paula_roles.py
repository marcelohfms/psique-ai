import asyncio
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
PHONE = "5581985580824"
async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    ct = await c.from_("contacts").select("*").eq("phone", PHONE).execute()
    for contact in (ct.data or []):
        print("CONTACT:", contact.get("id"), "| nome:", contact.get("name"), "| tel:", contact.get("phone"), "| cpf:", contact.get("cpf"))
        links = await c.from_("patient_contacts").select("*").eq("contact_id", contact["id"]).execute()
        print(f"  patient_contacts ({len(links.data or [])}):")
        for lk in (links.data or []):
            print("   ->", {k:lk.get(k) for k in ("patient_id","relationship","is_self","role_consulta","role_financeiro","role")})
            p = (await c.from_("patients").select("*").eq("id", lk["patient_id"]).execute()).data
            for pat in p:
                print("      PATIENT:", pat.get("id"), "| nome:", pat.get("name"), "| nasc:", pat.get("birth_date"), "| retornante:", pat.get("is_returning_patient"))
    # também: buscar todas as fichas com nome Paula p/ ver duplicidade
    print("\n-- pacientes com nome Paula --")
    pr = await c.from_("patients").select("id,name,birth_date").ilike("name","%Paula%").execute()
    for p in (pr.data or []):
        print("  ", p.get("id"), p.get("name"), p.get("birth_date"))
asyncio.run(main())
