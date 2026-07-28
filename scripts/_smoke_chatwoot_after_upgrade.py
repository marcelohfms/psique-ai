"""
Smoke test read-only da integração com o Chatwoot — para rodar depois de atualizar
a versão do Chatwoot e descobrir o que quebrou ANTES de um paciente descobrir.

Só faz GETs. Não envia mensagem, não cria nota, não altera label.

Uso:
    uv run python scripts/_smoke_chatwoot_after_upgrade.py
"""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE = os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")
ACCT = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
INBOX = os.getenv("CHATWOOT_INBOX_ID", "0")
BOT_TOKEN = os.getenv("CHATWOOT_AGENT_BOT_TOKEN", "")
USER_TOKEN = os.getenv("CHATWOOT_USER_TOKEN", "")
WEBHOOK_SECRET = os.getenv("CHATWOOT_WEBHOOK_SECRET", "")

API = f"{BASE}/api/v1/accounts/{ACCT}"

EVA_LABELS = {"eva-ativa", "eva-inativa"}

results: list[tuple[str, str, str]] = []  # (status, check, detail)


def ok(check, detail=""):
    results.append(("OK", check, detail))


def warn(check, detail=""):
    results.append(("WARN", check, detail))


def fail(check, detail=""):
    results.append(("FAIL", check, detail))


def h(token):
    return {"api_access_token": token, "Content-Type": "application/json"}


async def get(client, url, token, check, *, allow=(200,)):
    """GET + classifica o resultado. Retorna o JSON ou None."""
    try:
        r = await client.get(url, headers=h(token))
    except Exception as e:
        fail(check, f"{type(e).__name__}: {e}")
        return None
    if r.status_code not in allow:
        fail(check, f"HTTP {r.status_code} — {r.text[:200]}")
        return None
    try:
        return r.json()
    except Exception:
        fail(check, f"resposta não é JSON: {r.text[:200]}")
        return None


async def main():
    # ── 0. env ────────────────────────────────────────────────────────────────
    for name, val in [
        ("CHATWOOT_BASE_URL", BASE),
        ("CHATWOOT_ACCOUNT_ID", ACCT),
        ("CHATWOOT_INBOX_ID", INBOX),
        ("CHATWOOT_AGENT_BOT_TOKEN", BOT_TOKEN),
    ]:
        (ok if val else fail)(f"env {name}", "definida" if val else "AUSENTE")
    (ok if USER_TOKEN else warn)(
        "env CHATWOOT_USER_TOKEN",
        "definida" if USER_TOKEN else "ausente — cai no token do bot para search/labels",
    )
    (ok if WEBHOOK_SECRET else warn)(
        "env CHATWOOT_WEBHOOK_SECRET",
        "definida" if WEBHOOK_SECRET else "ausente — assinatura do webhook não é validada",
    )

    user_token = USER_TOKEN or BOT_TOKEN

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # ── 1. versão do Chatwoot ────────────────────────────────────────────
        info = await get(client, f"{BASE}/api", "", "versão do Chatwoot")
        if info:
            ok("versão do Chatwoot", f"{info.get('version')} ({info.get('edition', '?')})")

        # ── 2. autenticação dos dois tokens ──────────────────────────────────
        # O Chatwoot bloqueia tokens de bot em endpoints de leitura ("not authorized
        # for bots") — isso é esperado e não afeta a Eva, que só usa o token do bot
        # para POST de mensagem/nota. A validade dele é verificada no passo 9
        # (presença de mensagens recentes enviadas pelo agent_bot).
        warn(
            "auth token do AGENT BOT",
            "não verificável por GET (bots são bloqueados) — ver checagem 'Eva enviou mensagem recente'",
        )
        if USER_TOKEN:
            me_user = await get(client, f"{BASE}/api/v1/profile", USER_TOKEN, "auth CHATWOOT_USER_TOKEN")
            if me_user is not None:
                ok(
                    "auth CHATWOOT_USER_TOKEN",
                    f"id={me_user.get('id')} email={me_user.get('email')} role={me_user.get('role')}",
                )

        # ── 3. inbox ─────────────────────────────────────────────────────────
        inboxes = await get(client, f"{API}/inboxes", user_token, "listar inboxes")
        if inboxes:
            payload = inboxes.get("payload") or []
            match = [i for i in payload if str(i.get("id")) == str(INBOX)]
            if match:
                i = match[0]
                ok(
                    f"inbox {INBOX} existe",
                    f"name={i.get('name')} channel={i.get('channel_type')}",
                )
            else:
                fail(
                    f"inbox {INBOX} existe",
                    "IDs encontrados: " + ", ".join(f"{i.get('id')}({i.get('channel_type')})" for i in payload),
                )

        # ── 4. agent bot conectado ao inbox ──────────────────────────────────
        bots = await get(client, f"{API}/agent_bots", user_token, "agent_bots", allow=(200, 401, 403))
        if isinstance(bots, list):
            if bots:
                ok(
                    "agent_bots",
                    "; ".join(f"{b.get('name')} → {b.get('outgoing_url')}" for b in bots),
                )
            else:
                fail("agent_bots", "nenhum agent bot cadastrado — Eva não recebe mensagem nenhuma")
        elif bots is not None:
            warn("agent_bots", "sem permissão para listar com esse token (checar na UI)")

        # ── 5. webhooks de conta ─────────────────────────────────────────────
        hooks = await get(client, f"{API}/webhooks", user_token, "webhooks da conta", allow=(200, 401, 403))
        if isinstance(hooks, dict):
            payload = hooks.get("payload") or []
            if isinstance(payload, dict):  # Chatwoot >= 3.x aninha em {"webhooks": [...]}
                payload = payload.get("webhooks") or []
            if payload:
                for w in payload:
                    ok(
                        "webhook",
                        f"{w.get('url')} events={','.join(w.get('subscriptions') or [])}",
                    )
                subs = {s for w in payload for s in (w.get("subscriptions") or [])}
                for needed in ("message_created", "message_updated", "conversation_updated"):
                    (ok if needed in subs else warn)(
                        f"evento {needed}",
                        "inscrito" if needed in subs else "NÃO inscrito em nenhum webhook",
                    )
            else:
                warn("webhooks da conta", "nenhum webhook de conta (só o agent bot recebe eventos)")

        # ── 6. labels de controle da Eva ─────────────────────────────────────
        labels = await get(client, f"{API}/labels", user_token, "labels da conta")
        if labels:
            names = {l.get("title") for l in (labels.get("payload") or [])}
            missing = EVA_LABELS - names
            (ok if not missing else fail)(
                "labels eva-ativa/eva-inativa",
                "presentes" if not missing else f"faltando: {', '.join(sorted(missing))}",
            )

        # ── 7. busca de contato (find_or_create_conversation) ────────────────
        convs = await get(
            client, f"{API}/conversations?status=open&per_page=5", user_token, "listar conversas"
        )
        sample_conv = None
        sample_phone = None
        if convs:
            data = convs.get("data") or {}
            items = data.get("payload") or []
            ok("listar conversas", f"{len(items)} conversa(s) abertas na 1ª página")
            if items:
                sample_conv = items[0]
                sample_phone = (
                    (sample_conv.get("meta") or {}).get("sender", {}).get("phone_number") or ""
                ).lstrip("+")

        if sample_phone:
            found = await get(
                client,
                f"{API}/contacts/search?q={sample_phone}",
                user_token,
                "contacts/search",
            )
            if found is not None:
                hits = found.get("payload") or []
                (ok if hits else fail)(
                    "contacts/search",
                    f"{len(hits)} resultado(s) para telefone de amostra"
                    if hits
                    else "0 resultados para um telefone que existe — busca quebrada",
                )
                if len(hits) > 1:
                    warn(
                        "contacts/search duplicados",
                        f"{len(hits)} contatos para o mesmo telefone — risco de contato-sombra",
                    )
        else:
            warn("contacts/search", "nenhuma conversa aberta para usar de amostra")

        # ── 8. shape do payload de mensagem ──────────────────────────────────
        if sample_conv:
            cid = sample_conv.get("id")
            msgs = await get(
                client, f"{API}/conversations/{cid}/messages", user_token, "ler mensagens"
            )
            if msgs is not None:
                items = msgs.get("payload") if isinstance(msgs, dict) else msgs
                items = items or []
                ok("ler mensagens", f"{len(items)} mensagem(ns) na conversa {cid}")
                # ignora mensagens de atividade (message_type 2), que não têm sender
                real = [x for x in items if x.get("message_type") in (0, 1, "incoming", "outgoing")]
                if real:
                    m = real[-1]
                    for field in ("content", "message_type", "private", "sender"):
                        (ok if field in m else fail)(
                            f"campo '{field}' na mensagem",
                            repr(m.get(field))[:80] if field in m else "AUSENTE no payload da API",
                        )
                    mt = m.get("message_type")
                    (ok if isinstance(mt, int) else warn)(
                        "message_type é int",
                        f"{mt!r} ({type(mt).__name__}) — o código aceita int e string",
                    )
                    sender_phone = (m.get("sender") or {}).get("phone_number")
                    (ok if sender_phone is not None else warn)(
                        "sender.phone_number",
                        repr(sender_phone) if sender_phone else "ausente — cai no fallback conversation.meta.sender",
                    )

            conv_labels = await get(
                client, f"{API}/conversations/{cid}/labels", user_token, "ler labels da conversa"
            )
            if conv_labels is not None:
                ok("ler labels da conversa", repr(conv_labels.get("payload")))

        # ── 9. prova de vida: Eva enviou/recebeu algo recentemente? ──────────
        if convs:
            import datetime

            last_bot = None
            last_incoming = None
            for c in (convs.get("data") or {}).get("payload") or []:
                m = await get(
                    client,
                    f"{API}/conversations/{c['id']}/messages",
                    user_token,
                    f"mensagens conv {c['id']}",
                )
                if not m:
                    continue
                for x in (m.get("payload") if isinstance(m, dict) else m) or []:
                    ts = x.get("created_at") or 0
                    stype = (x.get("sender") or {}).get("type")
                    if stype == "agent_bot" and (not last_bot or ts > last_bot):
                        last_bot = ts
                    if x.get("message_type") == 0 and (not last_incoming or ts > last_incoming):
                        last_incoming = ts

            now = datetime.datetime.now().timestamp()
            for label, ts in [
                ("Eva enviou mensagem recente (token do bot funciona)", last_bot),
                ("paciente enviou mensagem recente (webhook de entrada)", last_incoming),
            ]:
                if not ts:
                    fail(label, "nenhuma encontrada nas conversas abertas")
                    continue
                mins = int((now - ts) / 60)
                when = datetime.datetime.fromtimestamp(ts).strftime("%d/%m %H:%M")
                (ok if mins < 180 else warn)(label, f"última em {when} ({mins} min atrás)")

    # ── relatório ────────────────────────────────────────────────────────────
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    print()
    for status, check, detail in results:
        print(f"{icon[status]} {check}" + (f" — {detail}" if detail else ""))
    fails = sum(1 for s, _, _ in results if s == "FAIL")
    warns = sum(1 for s, _, _ in results if s == "WARN")
    print(f"\n{len(results)} checagens: {len(results)-fails-warns} ok, {warns} avisos, {fails} falhas")
    if fails:
        print("\n⚠️  Falhas acima quebram a Eva em produção. Corrigir antes de deixar rodando.")
    print("\nNão testado aqui (precisa de ação que afeta paciente/atendente):")
    print("  • POST de mensagem outgoing (send_message / send_template_message)")
    print("  • nota privada (add_private_note)")
    print("  • entrega real do template de lembrete pela Meta")
    print("  → validar mandando UMA mensagem de teste do seu próprio WhatsApp para o número da clínica.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
