"""Luiza Siqueira Barbosa (558191183875): consulta ocorreu 15/07 às 10h e os dois
pagamentos entraram (R$100 em 01/07 + R$600 em 15/07), mas NÃO existe nenhuma
linha em `appointments`. Procura o evento nas duas agendas e mostra a conversa."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "558191183875"


async def main():
    from app.database import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    client = await get_supabase()
    svc = build("calendar", "v3", credentials=_credentials())

    docs = (await client.from_("doctors").select("doctor_id, name, agenda_id").execute()).data
    for d in docs:
        res = svc.events().list(
            calendarId=d["agenda_id"], timeMin="2026-07-15T00:00:00-03:00",
            timeMax="2026-07-16T00:00:00-03:00", singleEvents=True,
            showDeleted=True, orderBy="startTime").execute()
        hits = [e for e in res.get("items", [])
                if "luiza" in (e.get("summary", "") or "").lower()
                or "siqueira" in (e.get("summary", "") or "").lower()]
        print(f"\n=== {d['name']} ({d['agenda_id']}) — 15/07 ===")
        for e in res.get("items", []):
            mark = "  <<<" if e in hits else ""
            print(f"  {e.get('status'):9} {e.get('id')} {e.get('summary')} "
                  f"{(e.get('start') or {}).get('dateTime')}{mark}")

    print(f"\n=== conversa {PHONE} ===")
    msgs = (await client.from_("messages").select("*").eq("phone", PHONE)
            .order("created_at").execute()).data
    for m in msgs:
        dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m %H:%M")
        print(f"  {dt} | {m['role']:9} | {(m.get('content') or '')[:180]}")

asyncio.run(main())
