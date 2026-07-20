import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase, get_users_by_phone, _phone_variants
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    base = "5581995186399"
    seen_ids = set()
    for phone in _phone_variants(base):
        users = await get_users_by_phone(phone)
        print(f"\n=== variante {phone}: {len(users) if users else 0} registro(s) ===")
        if not users:
            continue
        for u in users:
            print("--- contato/paciente ---")
            for k, v in u.items():
                print(f"  {k}: {v}")

            pid = u.get("id")
            if pid:
                seen_ids.add(pid)

            appts = await client.from_("appointments").select("*").eq("patient_id", pid).order("start_time", desc=True).limit(10).execute()
            if appts.data:
                print("  Consultas:")
                for a in appts.data:
                    start = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                    print(f"    {start} | status={a['status']} | modality={a.get('modality')} | consultation_type={a.get('consultation_type')} | id={a['id']}")

    print("\n=== busca direta em contacts ===")
    for phone in _phone_variants(base):
        c = await client.from_("contacts").select("*").eq("phone", phone).execute()
        if c.data:
            for row in c.data:
                print(f"contacts row ({phone}):")
                for k, v in row.items():
                    print(f"  {k}: {v}")

    print("\n=== pagamentos (payments) ===")
    for pid in seen_ids:
        p = await client.from_("payments").select("*").eq("patient_id", pid).execute()
        if p.data:
            for row in p.data:
                print(f"payments row (patient_id={pid}):")
                for k, v in row.items():
                    print(f"  {k}: {v}")

asyncio.run(main())
