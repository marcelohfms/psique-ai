import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")

def fmt(iso):
    if not iso: return "-"
    try:
        return datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except: return iso

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()
    # created OR updated since 28/08 00:00 local
    since = datetime(2026,8,28,0,0,tzinfo=TZ).astimezone(timezone.utc).isoformat()
    r = await c.from_("appointments").select(
        "appointment_id,patient_id,contact_id,doctor_id,start_time,end_time,status,"
        "created_at,updated_at,paid_at,booking_fee_paid_at,booking_fee_waived,confirmed_at,"
        "payment_reminder_sent_at,refund_requested_at,refund_completed_at,consultation_type,"
        "pending_reschedule,reschedule_initiated_by,no_show_message_sent_at,modality"
    ).gte("created_at", since).order("created_at").execute()
    created = r.data or []

    r2 = await c.from_("appointments").select(
        "appointment_id,patient_id,contact_id,doctor_id,start_time,end_time,status,"
        "created_at,updated_at,paid_at,booking_fee_paid_at,booking_fee_waived,confirmed_at,"
        "payment_reminder_sent_at,refund_requested_at,refund_completed_at,consultation_type,"
        "pending_reschedule,reschedule_initiated_by,no_show_message_sent_at,modality"
    ).gte("updated_at", since).order("updated_at").execute()
    updated = r2.data or []

    # union by appointment_id
    allmap = {a["appointment_id"]: a for a in created}
    for a in updated: allmap.setdefault(a["appointment_id"], a)
    appts = list(allmap.values())

    # enrich patient names
    pids = list({a["patient_id"] for a in appts if a.get("patient_id")})
    names = {}
    if pids:
        pr = await c.from_("patients").select("id,name").in_("id", pids).execute()
        names = {p["id"]: p for p in (pr.data or [])}

    print(f"Total consultas criadas/atualizadas desde 28/08: {len(appts)}\n")
    doc = {}
    from app.graph.tools import DOCTOR_IDS
    inv = {v:k for k,v in DOCTOR_IDS.items()}
    for a in sorted(appts, key=lambda x: x.get("start_time") or ""):
        p = names.get(a.get("patient_id"), {})
        nm = p.get("name") or "(sem nome)"
        docn = inv.get(a.get("doctor_id"), a.get("doctor_id") or "?")
        flags = []
        if a["status"]=="scheduled" and not a.get("booking_fee_paid_at") and not a.get("booking_fee_waived"):
            flags.append("SEM TAXA/ISENÇÃO")
        if a.get("pending_reschedule"): flags.append(f"PENDING_RESCHEDULE({a.get('reschedule_initiated_by')})")
        if a.get("refund_requested_at") and not a.get("refund_completed_at"): flags.append("ESTORNO PENDENTE")
        if a.get("no_show_message_sent_at"): flags.append("NO_SHOW_MSG")
        print(f"[{a['status']:>10}] {fmt(a['start_time'])} {docn:6} {nm[:22]:22} "
              f"taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')} "
              f"| {' '.join(flags)}")
        print(f"             appt={a['appointment_id']} criado={fmt(a['created_at'])} atualizado={fmt(a['updated_at'])} pid={a.get('patient_id')} ")

asyncio.run(main())
