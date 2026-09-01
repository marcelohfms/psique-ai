import asyncio, os, re
from dotenv import load_dotenv
load_dotenv()

SINCE = os.environ.get("SINCE", "2026-08-27T00:00:00+00:00")
UNTIL = os.environ.get("UNTIL", "2100-01-01T00:00:00+00:00")

USER_HOUR = re.compile(r"\b(\d{1,2})\s*h(?:oras)?\b|\b[àa]s\s+(\d{1,2})\b|\b(\d{1,2})[:h]00\b")
CONFIRM_HOUR = re.compile(r"[àa]s\s+(\d{1,2})[:h]00")


def user_hours(text):
    hs = set()
    for m in USER_HOUR.finditer(text.lower()):
        h = m.group(1) or m.group(2) or m.group(3)
        if h and 6 <= int(h) <= 21:
            hs.add(int(h))
    return hs


async def main():
    from app.database import get_supabase
    client = await get_supabase()
    all_rows, offset, page = [], 0, 1000
    while True:
        res = (await client.table("messages").select("phone,role,content,created_at")
               .gte("created_at", SINCE).lt("created_at", UNTIL)
               .order("phone").order("created_at").range(offset, offset + page - 1).execute())
        rows = res.data or []
        all_rows.extend(rows)
        if len(rows) < page:
            break
        offset += page

    by_phone = {}
    for m in all_rows:
        by_phone.setdefault(m["phone"], []).append(m)

    flagged = []
    for phone, msgs in by_phone.items():
        msgs.sort(key=lambda m: m["created_at"])
        for i, m in enumerate(msgs):
            if m["role"] != "assistant":
                continue
            content = str(m.get("content") or "")
            if "confirmar antes de registrar" not in content.lower():
                continue
            cm = CONFIRM_HOUR.search(content.lower())
            if not cm:
                continue
            conf_h = int(cm.group(1))
            # ultima msg de usuario antes desta confirmacao
            prev_user = None
            for j in range(i - 1, max(-1, i - 4), -1):
                if msgs[j]["role"] == "user":
                    prev_user = str(msgs[j].get("content") or "")
                    break
            if not prev_user:
                continue
            uh = user_hours(prev_user)
            # paciente disse um horario explicito e a Eva confirmou OUTRO
            if uh and conf_h not in uh:
                flagged.append((phone, m["created_at"], sorted(uh), conf_h, prev_user[:60], content[:70]))

    print(f"=== {SINCE[:10]} a {UNTIL[:10]}: {len(flagged)} confirmações com horário trocado | {len(by_phone)} conversas ===")
    for phone, ts, uh, ch, pu, cc in flagged:
        print(f"  {phone} [{ts[:19]}] user={uh} -> Eva={ch}")
        print(f"      user: {pu!r}")

asyncio.run(main())
