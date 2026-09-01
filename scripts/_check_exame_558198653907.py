import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
TZ = ZoneInfo("America/Recife")

def fmt(iso):
    if not iso: return "-"
    try: return datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(TZ).strftime("%d/%m %H:%M")
    except: return iso

# 12-digit input, faltando 1 dígito. Testar variações de mobile (13 dígitos).
RAW = "558198653907"
VARIANTS = [
    RAW,
    "5581998653907",  # 55 81 99865-3907
    "5581986653907",  # 55 81 98665-3907
    "5581998653907",
]

async def main():
    from app.supabase_client import get_supabase
    c = await get_supabase()

    # 1) achar contatos por LIKE nos últimos dígitos
    tail = RAW[-8:]  # 98653907 -> usar 8653907
    print(f"=== Buscando contatos com telefone contendo {tail} ===")
    ct = await c.from_("contacts").select("*").ilike("phone", f"%{tail}%").execute()
    for x in ct.data or []:
        print(f"  contact_id={x.get('id')} phone={x.get('phone')} name={x.get('name')} active={x.get('active')} patient_id={x.get('patient_id')}")

    phones = sorted({x.get("phone") for x in (ct.data or []) if x.get("phone")} | set(VARIANTS))
    print("\n=== Telefones a checar ===", phones)

    for phone in phones:
        msgs = await c.from_("messages").select("*").eq("phone", phone).order("created_at").execute()
        if not msgs.data:
            continue
        print(f"\n===== MENSAGENS {phone} ({len(msgs.data)}) =====")
        for m in msgs.data:
            print(f" [{fmt(m.get('created_at'))}] {m.get('role')}: {str(m.get('content'))[:240]}")

        # documents (phone dentro do metadata)
        docs = await c.from_("documents").select("*").eq("metadata->>phone", phone).execute()
        print(f"\n----- DOCUMENTS {phone} ({len(docs.data or [])}) -----")
        for d in docs.data or []:
            print(f"  id={d.get('id')} content={d.get('content')} status={d.get('status')} meta={d.get('metadata')}")

        # events (phone pode estar em coluna ou metadata)
        ev = await c.from_("events").select("*").eq("metadata->>phone", phone).order("created_at").execute()
        print(f"\n----- EVENTS metadata.phone={phone} ({len(ev.data or [])}) -----")
        for e in ev.data or []:
            print(f"  {fmt(e.get('created_at'))} {e.get('event_type')} {str(e.get('metadata'))[:160]}")

asyncio.run(main())
