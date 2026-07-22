import asyncio, os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581991947587"


async def main():
    from app.database import get_supabase, _phone_variants, DOCTOR_NAMES
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    phones = _phone_variants(PHONE)
    print("Phone variants tried:", phones)

    print("\n" + "=" * 80)
    print("CONTACTS by phone")
    print("=" * 80)
    contact_ids = []
    for phone in phones:
        res = await client.from_("contacts").select("*").eq("phone", phone).execute()
        print(f"phone={phone}: {len(res.data)} row(s)")
        for c in res.data:
            print(" ", c)
            contact_ids.append(c["id"])

    contact_ids = list(dict.fromkeys(contact_ids))

    print("\n" + "=" * 80)
    print("PATIENTS by name ilike %LARISSA%")
    print("=" * 80)
    res = await client.from_("patients").select("*").ilike("name", "%LARISSA%").execute()
    for p in res.data:
        print(" ", p)

    print("\n" + "=" * 80)
    print("PATIENT_CONTACTS for contacts found above (with patient + contact detail)")
    print("=" * 80)
    patient_ids = set()
    for cid in contact_ids:
        pc = await client.from_("patient_contacts").select("*, patients(*)").eq("contact_id", cid).execute()
        print(f"contact_id={cid}: {len(pc.data)} link(s)")
        for row in pc.data:
            patient = row.get("patients") or {}
            print("  role:", row.get("role"), "is_self:", row.get("is_self"),
                  "relationship:", row.get("relationship"), "patient_id:", row.get("patient_id"))
            print("  patient:", patient)
            if patient.get("id"):
                patient_ids.add(patient["id"])

    print("\nAll linked patient_ids:", patient_ids)

    print("\n" + "=" * 80)
    print("APPOINTMENTS for each linked patient_id (all statuses, ordered by start_time)")
    print("=" * 80)
    for pid in patient_ids:
        appts = await client.from_("appointments").select("*").eq("patient_id", pid).order("start_time", desc=False).execute()
        print(f"\n--- patient_id={pid}: {len(appts.data)} appointment(s) ---")
        for a in appts.data:
            st = a.get("start_time")
            st_fmt = datetime.fromisoformat(st).astimezone(TZ).strftime("%d/%m/%Y %H:%M") if st else None
            doc = DOCTOR_NAMES.get(a.get("doctor_id") or "", a.get("doctor_id"))
            print(f"  appt={a['appointment_id']} | start={st_fmt} | status={a['status']} | doctor={doc} | "
                  f"type={a.get('consultation_type')} | "
                  f"created_at={a.get('created_at')} | updated_at={a.get('updated_at')}")
            print(f"    full row: {a}")

    print("\n" + "=" * 80)
    print("LANGGRAPH CHECKPOINT STATE for this phone's thread(s)")
    print("=" * 80)
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from app.graph.graph import build_graph

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    pg_conn = await AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row
    )
    checkpointer = AsyncPostgresSaver(pg_conn)
    graph = build_graph(checkpointer=checkpointer)

    keys_of_interest = [
        "user_name", "is_patient", "_is_patient_confirmed", "patient_name",
        "birth_date", "is_returning_patient", "patient_cpf", "patient_age",
        "guardian_name", "guardian_cpf", "preferred_doctor", "patient_email", "stage",
        "appointment_id", "consultation_type", "reschedule_in_progress",
        "reschedule_initiated_by", "confirmed_by_patient", "patient_id", "contact_id",
    ]

    for thread_suffix in phones:
        thread_id = f"{thread_suffix}@s.whatsapp.net"
        config = {"configurable": {"thread_id": thread_id}}
        snap = await graph.aget_state(config)
        print(f"\n--- thread_id={thread_id} ---")
        if not snap.values:
            print("  (no checkpoint state found)")
            continue
        for k in keys_of_interest:
            if k in snap.values:
                print(f"  {k}: {snap.values.get(k)!r}")
        print("\n  Últimas 20 mensagens no checkpoint:")
        for m in snap.values.get("messages", [])[-20:]:
            content = str(getattr(m, "content", ""))[:300]
            print(f"    {type(m).__name__}: {content}")

    await pg_conn.close()

    print("\n" + "=" * 80)
    print("RAW messages table (last 20) for this phone")
    print("=" * 80)
    for phone in phones:
        msgs = await client.from_("messages").select("*").eq("phone", phone).order("created_at", desc=True).limit(20).execute()
        if not msgs.data:
            continue
        print(f"\n--- phone={phone}: {len(msgs.data)} row(s) (most recent first, showing chronological) ---")
        for m in reversed(msgs.data):
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M:%S")
            print(f"  {dt} | {m.get('role')} | {(m.get('content') or '')[:300]}")

    print("\n" + "=" * 80)
    print("CHATWOOT conversation (last messages) for context")
    print("=" * 80)
    try:
        import httpx
        from app import chatwoot as cw
        async with httpx.AsyncClient(timeout=15) as hclient:
            contact = await cw._search_contact(hclient, PHONE)
            if not contact:
                print("Contato não encontrado no Chatwoot para", PHONE)
            else:
                print(f"Contato Chatwoot: id={contact.get('id')} name={contact.get('name')} phone={contact.get('phone_number')}")
                conv = await cw._find_conversation_for_contact(hclient, contact["id"])
                print("Conversa:", conv)
                if conv:
                    conv_id = conv[0]
                    url = f"{cw._base_url()}/api/v1/accounts/{cw._account_id()}/conversations/{conv_id}/messages"
                    resp = await hclient.get(url, headers=cw._headers())
                    resp.raise_for_status()
                    data = resp.json().get("payload") or {}
                    messages = data if isinstance(data, list) else (data.get("messages") or [])
                    print(f"Total de mensagens na conversa: {len(messages)}")
                    import datetime as dtmod
                    last20 = messages[-20:] if len(messages) > 20 else messages
                    for m in last20:
                        ts = m.get("created_at")
                        dt = dtmod.datetime.fromtimestamp(ts, tz=dtmod.timezone.utc).astimezone(TZ) if ts else None
                        content = (m.get("content") or "")[:300]
                        sender = m.get("sender", {}).get("name") if m.get("sender") else None
                        print(f"  [{dt}] type={m.get('message_type')} private={m.get('private')} sender={sender} content={content!r}")
    except Exception as e:
        print("Chatwoot lookup failed:", repr(e))


asyncio.run(main())
