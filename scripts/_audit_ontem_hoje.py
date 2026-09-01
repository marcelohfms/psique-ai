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
    from app.graph.tools import DOCTOR_IDS
    inv = {v:k for k,v in DOCTOR_IDS.items()}
    c = await get_supabase()

    # janela: 31/08 00:00 -> agora, horário de Recife
    since = datetime(2026,8,31,0,0,tzinfo=TZ).astimezone(timezone.utc).isoformat()

    # ---------- 1) conversas ativas (messages) ----------
    mr = await c.from_("messages").select("phone,role,content,created_at").gte("created_at", since).order("created_at").execute()
    msgs = mr.data or []
    phones = {}
    for m in msgs:
        phones.setdefault(m["phone"], []).append(m)
    print(f"Conversas ativas 31/08–hoje: {len(phones)} telefones, {len(msgs)} mensagens\n")

    # ---------- 2) consultas criadas/atualizadas na janela ----------
    cols = ("appointment_id,patient_id,contact_id,doctor_id,start_time,end_time,status,"
            "created_at,updated_at,paid_at,booking_fee_paid_at,booking_fee_waived,confirmed_at,"
            "payment_reminder_sent_at,refund_requested_at,refund_completed_at,consultation_type,"
            "pending_reschedule,reschedule_initiated_by,no_show_message_sent_at,modality")
    r = await c.from_("appointments").select(cols).gte("created_at", since).execute()
    r2 = await c.from_("appointments").select(cols).gte("updated_at", since).execute()
    allmap = {a["appointment_id"]: a for a in (r.data or [])}
    for a in (r2.data or []): allmap.setdefault(a["appointment_id"], a)
    appts = list(allmap.values())

    pids = list({a["patient_id"] for a in appts if a.get("patient_id")})
    names = {}
    if pids:
        pr = await c.from_("patients").select("id,name,birth_date,custom_price").in_("id", pids).execute()
        names = {p["id"]: p for p in (pr.data or [])}
    cids = list({a["contact_id"] for a in appts if a.get("contact_id")})
    cphone = {}
    if cids:
        cr = await c.from_("contacts").select("id,phone").in_("id", cids).execute()
        cphone = {x["id"]: x.get("phone") for x in (cr.data or [])}

    print(f"=== CONSULTAS criadas/atualizadas ({len(appts)}) ===")
    for a in sorted(appts, key=lambda x: x.get("start_time") or ""):
        p = names.get(a.get("patient_id"), {})
        nm = (p.get("name") or "(sem nome)")[:22]
        docn = inv.get(a.get("doctor_id"), "?")
        flags = []
        if a["status"]=="scheduled" and not a.get("booking_fee_paid_at") and not a.get("booking_fee_waived") and (p.get("custom_price") or 1)!=0:
            flags.append("SCHEDULED_SEM_TAXA")
        if a.get("pending_reschedule"): flags.append(f"PENDING_RESCHEDULE({a.get('reschedule_initiated_by')})")
        if a.get("refund_requested_at") and not a.get("refund_completed_at"): flags.append("ESTORNO_PENDENTE")
        if a.get("no_show_message_sent_at"): flags.append("NO_SHOW")
        if not p.get("birth_date"): flags.append("SEM_NASCIMENTO")
        tag = ("  <<< "+" ".join(flags)) if flags else ""
        print(f"[{a['status']:>10}] {fmt(a['start_time'])} {docn:5} {nm:22} taxa={fmt(a.get('booking_fee_paid_at'))} isento={a.get('booking_fee_waived')}{tag}")
        if flags:
            print(f"             appt={a['appointment_id']} pid={a.get('patient_id')} tel={cphone.get(a.get('contact_id'))} criado={fmt(a['created_at'])} atualizado={fmt(a['updated_at'])}")

    # ---------- 3) telefones que conversaram mas SEM consulta na janela ----------
    appt_phones = {cphone.get(a.get("contact_id")) for a in appts}
    print("\n=== SINAIS nas conversas (heurística) ===")
    for phone, ms in phones.items():
        last = ms[-1]
        # última mensagem foi do paciente e nunca respondida? (possível Eva muda)
        user_msgs = [m for m in ms if m["role"]=="user"]
        assistant_msgs = [m for m in ms if m["role"]=="assistant"]
        joined = " ".join(m["content"].lower() for m in ms if m.get("content"))
        sig = []
        if last["role"]=="user":
            sig.append("ULTIMA_DO_PACIENTE")
        if "comprovante" in joined and "registrado com sucesso" not in joined and "registrado pela atendente" not in joined:
            sig.append("COMPROVANTE?")
        if any(w in joined for w in ["reclama","erro","errado","não recebi","nao recebi","cadê","cade "]):
            sig.append("QUEIXA?")
        if sig:
            print(f"{phone:16} msgs={len(ms):3} u={len(user_msgs)} a={len(assistant_msgs)} last={fmt(last['created_at'])} {last['role']:9} :: {' '.join(sig)}")

asyncio.run(main())
