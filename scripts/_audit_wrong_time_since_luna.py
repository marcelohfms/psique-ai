import asyncio
import re
from dotenv import load_dotenv
load_dotenv()

SINCE = "2026-08-27T00:00:00+00:00"

# Sinais de que o PACIENTE está corrigindo a Eva sobre horário/data.
USER_CORRECTION = [
    r"n[ãa]o [ée] [àa]s", r"n[ãa]o era", r"a consulta n[ãa]o", r"eu pedi",
    r"eu marquei", r"combinamos", r"marcamos", r"hor[áa]rio errado",
    r"n[ãa]o [ée] esse", r"n[ãa]o [ée] esse hor", r"tinha marcado",
    r"era [àa]s", r"[ée] [àa]s \d", r"n[ãa]o [ée] no dia", r"n[ãa]o [ée] essa",
    r"est[áa] errad", r"trocou", r"mudou o hor", r"outro hor",
]
USER_RE = re.compile("|".join(USER_CORRECTION), re.IGNORECASE)


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Puxa mensagens de usuário desde SINCE (paginado).
    hits = []
    offset = 0
    page = 1000
    while True:
        res = (
            await client.table("messages")
            .select("phone, role, content, created_at")
            .eq("role", "user")
            .gte("created_at", SINCE)
            .order("created_at")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = res.data or []
        for m in rows:
            content = str(m.get("content") or "")
            if USER_RE.search(content):
                hits.append(m)
        if len(rows) < page:
            break
        offset += page

    # Agrupa por telefone
    by_phone = {}
    for h in hits:
        by_phone.setdefault(h["phone"], []).append(h)

    print(f"=== {len(hits)} mensagens suspeitas em {len(by_phone)} conversas (desde {SINCE}) ===\n")
    for phone, msgs in by_phone.items():
        print(f"### {phone} ({len(msgs)} hits)")
        for m in msgs:
            print(f"   [{m['created_at']}] user: {str(m['content'])[:160]}")
        print()

asyncio.run(main())
