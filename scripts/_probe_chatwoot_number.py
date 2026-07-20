"""Diagnóstico de resolução de conversa no Chatwoot para UM número.

Só LÊ/RESOLVE a conversa (search de contato + listagem de conversas). NÃO envia
nenhuma mensagem WhatsApp. Serve para diagnosticar por que o cron de lembrete
dá timeout ao resolver a conversa de um número específico.

Uso:
    uv run python scripts/_probe_chatwoot_number.py 5581988054825
    PROBE_PHONE=5581988054825 uv run python scripts/_probe_chatwoot_number.py

Roda no CI via workflow_dispatch (.github/workflows/probe_chatwoot.yml), onde há
acesso de rede ao Chatwoot.
"""
import asyncio
import os
import sys
import time
import traceback

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — evita import circular


async def _timed(label: str, coro):
    t = time.monotonic()
    try:
        result = await coro
        print(f"[{label}] OK em {time.monotonic() - t:.1f}s -> {result!r}")
        return result
    except Exception as e:
        print(f"[{label}] FALHOU após {time.monotonic() - t:.1f}s: {type(e).__name__}: {e!r}")
        traceback.print_exc()
        raise


async def main():
    import httpx
    from app.chatwoot import (
        _base_url, _account_id, _headers, _inbox_id, _strip_phone,
        _phone_variants, _search_contact, _find_conversation_for_contact,
    )

    raw = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PROBE_PHONE", "")
    if not raw:
        print("Informe o número: argv[1] ou PROBE_PHONE")
        sys.exit(2)
    phone = raw if "@s.whatsapp.net" in raw else f"{raw}@s.whatsapp.net"
    digits = _strip_phone(phone)
    print(f"phone={phone} digits={digits} variants={_phone_variants(digits)} inbox_cfg={_inbox_id()}")

    base, acc, headers = _base_url(), _account_id(), _headers()

    # timeout=30 (vs 10 do código) para distinguir 'lento' de 'inacessível'.
    async with httpx.AsyncClient(timeout=30) as client:
        # 1) search por variante — mede cada chamada isoladamente
        for v in _phone_variants(digits):
            url = f"{base}/api/v1/accounts/{acc}/contacts/search"
            t = time.monotonic()
            try:
                resp = await client.get(url, params={"q": v, "include": "contact_inboxes"}, headers=headers)
                payload = resp.json().get("payload") or []
                print(f"[search v={v}] {time.monotonic() - t:.1f}s status={resp.status_code} "
                      f"n={len(payload)} ids={[(p.get('id'), bool(p.get('contact_inboxes'))) for p in payload]}")
            except Exception as e:
                print(f"[search v={v}] FALHOU após {time.monotonic() - t:.1f}s: {type(e).__name__}: {e!r}")

        # 2) _search_contact (escolha final do contato)
        try:
            contact = await _timed("_search_contact", _search_contact(client, digits))
        except Exception:
            return
        if not contact:
            print("Nenhum contato encontrado; encerrando (probe não cria contato).")
            return
        contact_id = contact.get("id")

        # 3) listagem de conversas do contato
        conv_url = f"{base}/api/v1/accounts/{acc}/contacts/{contact_id}/conversations"
        t = time.monotonic()
        try:
            resp = await client.get(conv_url, headers=headers)
            convs = resp.json().get("payload") or []
            print(f"[conversations] {time.monotonic() - t:.1f}s status={resp.status_code} "
                  f"n={len(convs)} -> {[(c.get('id'), c.get('inbox_id'), c.get('status')) for c in convs]}")
        except Exception as e:
            print(f"[conversations] FALHOU após {time.monotonic() - t:.1f}s: {type(e).__name__}: {e!r}")

        # 4) _find_conversation_for_contact (o que o código usa)
        try:
            await _timed("_find_conversation_for_contact", _find_conversation_for_contact(client, contact_id))
        except Exception:
            return


if __name__ == "__main__":
    asyncio.run(main())
