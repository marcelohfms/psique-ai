import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # achar patient Ruda
    pats = await client.from_("patients").select("id,name,birth_date,is_returning_patient").ilike("name", "%Rudá%").execute()
    pats2 = await client.from_("patients").select("id,name,birth_date,is_returning_patient").ilike("name", "%Ruda%").execute()
    seen = {}
    for p in (pats.data + pats2.data):
        seen[p["id"]] = p
    print("=== pacientes ===")
    for p in seen.values():
        print(" ", p)

    for pid in seen:
        appts = await client.from_("appointments").select("*").eq("patient_id", pid).order("scheduled_at", desc=False).execute()
        print(f"\n=== appointments patient_id={pid} ({seen[pid]['name']}) ===")
        for a in appts.data:
            sa = a.get("scheduled_at")
            if sa:
                sa = datetime.fromisoformat(sa).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  id={a['id']} | {sa} | status={a.get('status')} | doctor={a.get('doctor')} | type={a.get('consultation_type')} | created={a.get('created_at','')[:19]} | google_event={a.get('google_event_id')}")

asyncio.run(main())
