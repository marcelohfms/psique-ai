import asyncio, json
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("America/Recife")
    client = await get_supabase()

    # Descobre colunas reais da tabela checkpoints
    r = await client.from_("checkpoints").select("*").limit(1).execute()
    if r.data:
        print("Colunas checkpoints:", list(r.data[0].keys()))

    # Testa variantes
    for t in ["558195302944", "558195302944@s.whatsapp.net", "5581995302944", "5581995302944@s.whatsapp.net"]:
        r = await client.from_("checkpoints").select("thread_id, checkpoint_id") \
            .eq("thread_id", t).limit(3).execute()
        if r.data:
            print(f"✅ {t}: {r.data}")
        else:
            print(f"  ✗ {t}")

    # Mensagens com roles reais
    print("\n--- Mensagens (roles reais) ---")
    msgs = await client.from_("messages").select("role, content, created_at") \
        .eq("phone", "558195302944").order("created_at").execute()
    for m in msgs.data:
        ts = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        content = (m["content"] or "")[:120].replace("\n", " ")
        print(f"  {ts} [{m['role']:9}] {content}")

asyncio.run(main())
