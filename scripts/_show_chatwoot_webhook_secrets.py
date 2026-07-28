"""
Imprime os secrets HMAC dos DOIS emissores que entregam em /chatwoot-webhook,
já no formato da env var (separados por vírgula).

São dois porque o Chatwoot trata webhook de conta e agent bot como entidades
separadas, cada uma com seu próprio secret. Configurar só um rejeita metade do
tráfego — e a metade do agent bot é a que aciona a Eva.

Uso:
    uv run python scripts/_show_chatwoot_webhook_secrets.py

Somente leitura. Imprime credenciais no terminal: não cole a saída em lugar
público (issue, PR, chat) — só no campo de env var do Easypanel.
"""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")
ACCOUNT = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
TOKEN = os.getenv("CHATWOOT_USER_TOKEN") or os.getenv("CHATWOOT_AGENT_BOT_TOKEN", "")
BOT_URL_MARKER = "/chatwoot-webhook"


def _payload(resp: httpx.Response):
    data = resp.json()
    data = data.get("payload", data) if isinstance(data, dict) else data
    if isinstance(data, dict):
        data = data.get("webhooks", data)
    return data


def main() -> int:
    if not BASE or not TOKEN:
        print("CHATWOOT_BASE_URL / token ausentes no .env", file=sys.stderr)
        return 1

    headers = {"api_access_token": TOKEN}
    api = f"{BASE}/api/v1/accounts/{ACCOUNT}"
    secrets: list[str] = []

    hooks = _payload(httpx.get(f"{api}/webhooks", headers=headers, timeout=20))
    for h in hooks:
        marker = " ← entrega na Eva" if BOT_URL_MARKER in h.get("url", "") else ""
        print(f"webhook   id={h.get('id')}  url={h.get('url')}{marker}")
        if BOT_URL_MARKER in h.get("url", "") and h.get("secret"):
            print(f"          secret={h['secret']}")
            secrets.append(h["secret"])

    bots = _payload(httpx.get(f"{api}/agent_bots", headers=headers, timeout=20))
    for b in bots:
        marker = " ← entrega na Eva" if BOT_URL_MARKER in (b.get("outgoing_url") or "") else ""
        print(f"agent_bot id={b.get('id')}  name={b.get('name')}  url={b.get('outgoing_url')}{marker}")
        if BOT_URL_MARKER in (b.get("outgoing_url") or "") and b.get("secret"):
            print(f"          secret={b['secret']}")
            secrets.append(b["secret"])

    if not secrets:
        print("\nNenhum emissor apontando para /chatwoot-webhook encontrado.", file=sys.stderr)
        return 1

    print(f"\n{len(secrets)} emissor(es) cobertos. Valor para a env var:\n")
    print(",".join(secrets))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
