import asyncio
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

SINCE = "2026-08-27T00:00:00+00:00"
WINDOW_MIN = 40  # minutos

# So consideramos mensagens da Eva que sao confirmacao/registro de UMA consulta
CONFIRM_CTX = ["confirmar antes de registrar", "consulta registrada",
               "presença confirmada", "posso confirmar", "está agendada",
               "permanece", "agendada para"]

TIME_RE = re.compile(r"\b(\d{1,2})[:h]00\b|\b[àa]s\s+(\d{1,2})h?\b")


def parse(ts):
    return datetime.fromisoformat(ts)


def extract_time(text):
    t = text.lower()
    if not any(c in t for c in CONFIRM_CTX):
        return None
    times = set()
    for m in TIME_RE.finditer(t):
        h = m.group(1) or m.group(2)
        if h is None:
            continue
        h = int(h)
        if 6 <= h <= 21:
            times.add(h)
    return times or None


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    all_rows = []
    offset = 0
    page = 1000
    while True:
        res = (
            await client.table("messages")
            .select("phone, role, content, created_at")
            .gte("created_at", SINCE)
            .order("phone")
            .order("created_at")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page:
            break
        offset += page

    by_phone = {}
    for m in all_rows:
        by_phone.setdefault(m["phone"], []).append(m)

    print(f"=== {len(by_phone)} conversas desde {SINCE} | janela {WINDOW_MIN}min ===\n")

    flagged = []
    for phone, msgs in by_phone.items():
        msgs.sort(key=lambda m: m["created_at"])
        confs = []  # (dt, set_of_hours, snippet)
        for m in msgs:
            if m["role"] != "assistant":
                continue
            content = str(m.get("content") or "")
            hours = extract_time(content)
            if hours:
                confs.append((parse(m["created_at"]), hours, content[:110]))
        # compara confirmacoes dentro da janela
        for i in range(len(confs)):
            for j in range(i + 1, len(confs)):
                dt1, h1, s1 = confs[i]
                dt2, h2, s2 = confs[j]
                if dt2 - dt1 > timedelta(minutes=WINDOW_MIN):
                    break
                if h1 and h2 and h1.isdisjoint(h2):
                    flagged.append((phone, dt1, sorted(h1), sorted(h2), s1, s2))

    if not flagged:
        print("Nenhum flip de horário em confirmações dentro da janela.")
    seen = set()
    for phone, dt1, h1, h2, s1, s2 in flagged:
        key = (phone, tuple(h1), tuple(h2))
        if key in seen:
            continue
        seen.add(key)
        print(f"### {phone}  {dt1.isoformat()}  {h1} -> {h2}")
        print(f"     A: {s1}")
        print(f"     B: {s2}\n")

asyncio.run(main())
