import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")
def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(str(iso).replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
    except: return str(iso)

BENTO = "161c1e7f-c4f0-4e56-82f6-4ab2d7b11550"
DANIELLA_FICHA = "970df18e-268c-4454-b3ce-dc50882c9c6b"

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    for pid, label in [(BENTO,"BENTO (menor)"), (DANIELLA_FICHA,"DANIELLA ficha própria")]:
        ap = await c.from_("appointments").select("*").eq("patient_id",pid).order("start_time").execute()
        print(f"\n=== agendamentos {label} {pid} ===")
        for a in ap.data or []:
            print(f"  {fmt(a.get('start_time'))} status={a.get('status')} note={a.get('session_note')} "
                  f"waived={a.get('booking_fee_waived')} paid={fmt(a.get('booking_fee_paid_at'))} "
                  f"apptid={a.get('appointment_id')}")
    # mensagens do Sandro (pai) recentes
    msgs = await c.from_("messages").select("role,content,created_at").eq("phone","5581995397978").gte("created_at","2026-08-28").order("created_at").execute()
    print("\n=== mensagens SANDRO 5581995397978 desde 28/08 ===")
    for m in msgs.data or []:
        print(f"  {fmt(m['created_at'])} [{m['role']}] {str(m.get('content'))[:200]}")

asyncio.run(main())
