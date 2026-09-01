import asyncio
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
    pid="93a1bf04-c48d-4083-87e1-b24d3cbe3233"
    pc = await c.from_("patient_contacts").select("contact_id,is_self,relationship").eq("patient_id",pid).execute()
    phones=[]
    for x in pc.data or []:
        ct = await c.from_("contacts").select("phone,active,name").eq("id",x["contact_id"]).maybe_single().execute()
        cd=ct.data or {}
        print("CONTACT:", cd.get("phone"),"active=",cd.get("active"),"name=",cd.get("name"),"is_self=",x["is_self"],"rel=",x["relationship"])
        if cd.get("phone"): phones.append(cd["phone"])
    for ph in set(phones):
        ev = await c.from_("events").select("event_type,created_at,metadata").eq("phone",ph).gte("created_at","2026-08-24").order("created_at").execute()
        print(f"\n=== Eventos {ph} (desde 24/08) ===")
        for e in ev.data or []:
            print(f"  {fmt(e['created_at'])} {e['event_type']}: {str(e.get('metadata'))[:110]}")
        msgs = await c.from_("messages").select("role,content,created_at").eq("phone",ph).gte("created_at","2026-08-24").order("created_at").execute()
        print(f"\n=== Mensagens {ph} (desde 24/08) ===")
        for m in msgs.data or []:
            print(f"  {fmt(m['created_at'])} [{m['role']}] {str(m.get('content'))[:140]}")

asyncio.run(main())
