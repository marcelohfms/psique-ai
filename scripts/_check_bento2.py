import asyncio, json
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M:%S")
    except: return str(iso)

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    bento="161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"
    # all patient_contacts for Bento (with all cols)
    pc = await c.from_("patient_contacts").select("*").eq("patient_id",bento).execute()
    print("=== patient_contacts do Bento (raw) ===")
    for x in pc.data or []:
        cd=(await c.from_("contacts").select("phone,name").eq("id",x["contact_id"]).maybe_single().execute()).data or {}
        print(f"  {json.dumps(x, ensure_ascii=False)}  <phone {cd.get('phone')} / {cd.get('name')}>")
    # payments for the 02/09 appt
    print("\n=== payments do Bento ===")
    pay = await c.from_("payments").select("*").eq("patient_id",bento).order("created_at").execute() if False else None
    # try by appointment
    for appt in ["uvujd8pjg6aha3a7h6rfegnvmc","9f5dq513l813tk65i02te3vj88"]:
        p = await c.from_("payments").select("*").eq("appointment_id",appt).execute()
        for x in p.data or []:
            print(f"  appt={appt[:10]} {json.dumps({k:fmt(v) if 'at' in k or 'date' in k else v for k,v in x.items() if k in ('amount','type','method','created_at','paid_at','drive_url','sheet_row')}, ensure_ascii=False)}")
    # events since 28/08 for Daniella phone
    ph="5581991749847"
    ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).gte("created_at","2026-08-27").order("created_at").execute()
    print(f"\n=== eventos {ph} desde 27/08 ===")
    for e in ev.data or []:
        print(f"  {fmt(e['created_at'])} {e['event_type']}: {str(e.get('metadata'))[:110]}")

asyncio.run(main())
