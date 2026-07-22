import asyncio, json
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581991320003"

async def main():
    from app.database import get_supabase, get_users_by_phone, DOCTOR_NAMES
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    users = await get_users_by_phone(PHONE + "@s.whatsapp.net")
    print(f"get_users_by_phone -> {len(users)} users")
    pids = []
    for u in users:
        pids.append(u["id"])
        print("  patient_id:", u["id"], "name:", u.get("patient_name") or u.get("name"),
              "age:", u.get("patient_age"), "doctor:", DOCTOR_NAMES.get(u.get("doctor_id",""),""),
              "is_returning:", u.get("is_returning_patient"))

    if pids:
        appts = await client.from_("appointments").select(
            "appointment_id, patient_id, start_time, end_time, status, consultation_type, modality"
        ).in_("patient_id", pids).order("start_time", desc=True).limit(15).execute()
        print("\n=== appointments ===")
        for a in appts.data:
            st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            et = datetime.fromisoformat(a["end_time"]).astimezone(TZ).strftime("%H:%M") if a.get("end_time") else "?"
            print(f"  {st}-{et} | status={a['status']} | type={a.get('consultation_type')} | mod={a.get('modality')} | evt={a['appointment_id']}")

    print("\n=== tool_call messages ===")
    msgs = await client.from_("messages").select("*").eq("phone", PHONE).order("created_at", desc=False).execute()
    for m in msgs.data:
        role = m.get("role")
        content = (m.get("content") or "")
        if role in ("user", "assistant"):
            # print short
            print(f"[{m.get('created_at','')[:19]}] {role}: {content[:300]}")
        else:
            print(f"[{m.get('created_at','')[:19]}] {role}: {json.dumps(m, default=str)[:400]}")
    print("total msgs:", len(msgs.data))

asyncio.run(main())
