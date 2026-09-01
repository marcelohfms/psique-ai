import asyncio
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

SINCE = "2026-08-27T00:00:00+00:00"

# Marcadores de fluxo de remarcação (lado da Eva)
RESCHEDULE_MARKERS = [
    "aproveitada normalmente para a nova data",
    "vale para uma remarcação",
    "nova taxa de reserva",
]
CONFIRM_PROMPT = "confirmar antes de registrar"
REGISTERED = "consulta registrada"


def parse(ts):
    return datetime.fromisoformat(ts)


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    # Puxa TODAS as mensagens desde SINCE, paginado, ordenadas por telefone+tempo
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

    print(f"=== varrendo {len(by_phone)} conversas desde {SINCE} ===\n")

    flagged = []
    for phone, msgs in by_phone.items():
        msgs.sort(key=lambda m: m["created_at"])
        for i, m in enumerate(msgs):
            content = str(m.get("content") or "").lower()
            if m["role"] != "assistant":
                continue
            # remarcação fantasma: marcador de remarcação logo apos "consulta registrada"
            # sem o usuario ter pedido remarcacao no intervalo
            if any(mk in content for mk in RESCHEDULE_MARKERS):
                # procura "consulta registrada" nas ultimas 6 msgs anteriores
                window = msgs[max(0, i - 6):i]
                had_register = any(REGISTERED in str(w.get("content") or "").lower()
                                   for w in window if w["role"] == "assistant")
                user_reschedule = any(
                    any(k in str(w.get("content") or "").lower()
                        for k in ["remarc", "mudar", "trocar", "outro dia", "outro hor", "adiar", "antecipar"])
                    for w in window if w["role"] == "user"
                )
                if had_register and not user_reschedule:
                    flagged.append((phone, m["created_at"], "remarcacao-fantasma"))

    if not flagged:
        print("Nenhum caso de remarcação-fantasma pós-agendamento encontrado.")
    for phone, ts, kind in flagged:
        print(f"### {phone}  [{ts}]  {kind}")

asyncio.run(main())
