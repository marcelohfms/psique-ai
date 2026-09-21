# E-mail HTML do relatório de funil — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o corpo do e-mail do relatório de funil de texto puro para HTML (visual aprovado), mantendo um fallback em texto, sem mudar cálculo, destinatário ou periodicidade.

**Architecture:** Uma função pura nova `build_html_report` monta o HTML com estilos inline. `_send_email` ganha um `html_body` opcional e passa a anexar texto + HTML (multipart/alternative). O cron gera as duas versões e envia. Caminho de e-mail da clínica intacto.

**Tech Stack:** Python, email.mime, pytest.

**Referências de leitura:**
- `app/funnel_report.py` — `compute_funnel`, `format_report` (texto) e as constantes; `build_html_report` será adicionada aqui.
- `app/email_sender.py` — `_send_email` (linha ~23) e `send_report_email`.
- `scripts/send_funnel_report.py` — a chamada `format_report` + `send_report_email`.
- `docs/superpowers/specs/2026-09-21-relatorio-funil-email-html-design.md` — o visual aprovado.

**Convenções:** rodar dentro da worktree `.worktrees/funil-email-html`. Testes: `uv run pytest --tb=short`. Commits pequenos por tarefa, cada um terminando com `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Modify** `app/funnel_report.py` — nova `build_html_report(result, now, tz)` pura + helpers de largura de barra e pill de etapa. `format_report` (texto) fica.
- **Modify** `app/email_sender.py` — `_send_email` ganha `html_body` opcional; `send_report_email` idem.
- **Modify** `scripts/send_funnel_report.py` — gerar html e passar `html_body`.
- **Modify** `tests/test_funnel_report.py` — testes de `build_html_report`.
- **Modify** `tests/test_funnel_report_cron.py` — `_send_email` com/sem html; cron passa `html_body`.

---

## Task 1: `build_html_report` em `app/funnel_report.py`

**Files:**
- Modify: `app/funnel_report.py`
- Test: `tests/test_funnel_report.py`

- [ ] **Step 1: APÊNDICE ao final de `tests/test_funnel_report.py`:**

```python
# ── build_html_report ─────────────────────────────────────────────────────────

from app.funnel_report import build_html_report


def test_build_html_report_has_numbers_and_pendentes():
    result = {
        "interessados": 50, "qualificados": 28, "agendados": 22, "conversao_pct": 44,
        "pendentes": [{"phone": "5581999991234", "etapa": "Agendou, falta pagar", "name": "Mariana"},
                      {"phone": "5581999995678", "etapa": "Qualificado", "name": ""}],
        "perdidos": 21,
    }
    html = build_html_report(result, NOW, TZ)
    assert html.lstrip().startswith("<")
    assert "44%" in html
    assert "22 de 50" in html or "22</" in html
    assert "Mariana" in html and "5581999991234" in html
    assert "5581999995678" in html          # pendente sem nome usa o telefone
    assert "Agendou, falta pagar" in html
    assert "text/html" not in html          # é fragmento HTML, não MIME
    assert "<script" not in html            # e-mail: sem JS


def test_build_html_report_escapes_name():
    result = {
        "interessados": 1, "qualificados": 0, "agendados": 0, "conversao_pct": 0,
        "pendentes": [{"phone": "5581", "etapa": "Interessado", "name": "A & <b>"}],
        "perdidos": 0,
    }
    html = build_html_report(result, NOW, TZ)
    assert "A &amp; &lt;b&gt;" in html      # nome escapado


def test_build_html_report_empty_cohort_no_crash():
    result = {"interessados": 0, "qualificados": 0, "agendados": 0, "conversao_pct": 0,
              "pendentes": [], "perdidos": 0}
    html = build_html_report(result, NOW, TZ)
    assert "0%" in html
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report.py -q`
Expected: FAIL com ImportError (`build_html_report` não existe).

- [ ] **Step 3: Em `app/funnel_report.py`, adicionar no topo o import do módulo `html` (junto aos imports existentes):**

```python
import html as _html
```

E adicionar ao FINAL de `app/funnel_report.py`:

```python
# Cores da "pill" por etapa: (fundo, texto).
_ETAPA_PILL = {
    "Agendado": ("#dcfce7", "#166534"),
    "Agendou, falta pagar": ("#fef3c7", "#92400e"),
    "Qualificado": ("#e0f2fe", "#075985"),
    "Interessado": ("#f1f5f9", "#475569"),
}


def _bar_width_pct(part: int, whole: int) -> int:
    """Largura da barra em %, com mínimo visual de 6% para valor > 0 nunca sumir."""
    if not whole or part <= 0:
        return 0
    return max(6, round(100 * part / whole))


def _pendente_row(p: dict) -> str:
    nome = _html.escape((p.get("name") or "").strip())
    phone = _html.escape(p.get("phone") or "")
    quem = nome if nome else phone
    etapa = p.get("etapa") or ""
    bg, fg = _ETAPA_PILL.get(etapa, ("#f1f5f9", "#475569"))
    etapa_html = (
        f'<span style="background:{bg};color:{fg};padding:2px 8px;'
        f'border-radius:10px;font-size:12px;">{_html.escape(etapa)}</span>'
    )
    return (
        '<tr>'
        f'<td style="padding:8px 10px;color:#0f172a;border-bottom:1px solid #f1f5f9;">{quem}</td>'
        f'<td style="padding:8px 10px;color:#334155;border-bottom:1px solid #f1f5f9;">{phone}</td>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #f1f5f9;">{etapa_html}</td>'
        '</tr>'
    )


def build_html_report(result: dict, now: datetime, tz) -> str:
    """Monta o corpo HTML do relatório (estilos inline, layout em tabela, sem JS
    nem CSS externo — seguro para clientes de e-mail). Visual aprovado no mockup."""
    hoje = now.astimezone(tz).strftime("%d/%m/%Y")
    inter = result["interessados"]
    qual = result["qualificados"]
    agen = result["agendados"]
    conv = result["conversao_pct"]
    q_drop = inter - qual
    a_drop = qual - agen
    qual_w = _bar_width_pct(qual, inter)
    agen_w = _bar_width_pct(agen, inter)

    q_drop_html = (
        f'<div style="font-size:11px;color:#ef4444;margin:0 0 8px 130px;">'
        f'↓ {q_drop} desistiram no cadastro</div>' if q_drop > 0 else ""
    )
    a_drop_html = (
        f'<div style="font-size:11px;color:#ef4444;margin:0 0 4px 130px;">'
        f'↓ {a_drop} viram horário e não pagaram</div>' if a_drop > 0 else ""
    )

    if result["pendentes"]:
        rows = "".join(_pendente_row(p) for p in result["pendentes"])
    else:
        rows = ('<tr><td colspan="3" style="padding:8px 10px;color:#94a3b8;'
                'font-size:13px;">(nenhum)</td></tr>')

    return f"""\
<div style="background:#eef1f4;padding:24px;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;">
    <tr><td style="background:#0f766e;padding:20px 24px;">
      <div style="color:#ffffff;font-size:18px;font-weight:700;">Funil de leads — Clínica Psique</div>
      <div style="color:#b8e0db;font-size:13px;margin-top:2px;">Semana de {hoje} · últimos {FUNNEL_WINDOW_DAYS} dias</div>
    </td></tr>

    <tr><td style="padding:24px 24px 8px 24px;text-align:center;">
      <div style="font-size:40px;font-weight:800;color:#0f766e;line-height:1;">{conv}%</div>
      <div style="font-size:13px;color:#64748b;margin-top:4px;">conversão · {agen} de {inter} leads viraram consulta paga</div>
    </td></tr>

    <tr><td style="padding:16px 24px 8px 24px;">
      <div style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;">O funil</div>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Interessados</td>
        <td><div style="background:#3b82f6;height:26px;width:100%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{inter}</div></td>
      </tr></table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:2px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Qualificados</td>
        <td><div style="background:#0ea5e9;height:26px;width:{qual_w}%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{qual}</div></td>
      </tr></table>
      {q_drop_html}

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:2px;"><tr>
        <td style="width:130px;font-size:13px;color:#334155;">Agendados (pagos)</td>
        <td><div style="background:#0f766e;height:26px;width:{agen_w}%;border-radius:5px;color:#fff;font-size:13px;font-weight:700;line-height:26px;padding-left:10px;">{agen}</div></td>
      </tr></table>
      {a_drop_html}
    </td></tr>

    <tr><td style="padding:16px 24px 8px 24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="width:50%;padding-right:6px;">
          <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:12px;text-align:center;">
            <div style="font-size:24px;font-weight:800;color:#ea580c;">{len(result['pendentes'])}</div>
            <div style="font-size:12px;color:#9a3412;">Pendentes (quentes)</div>
          </div>
        </td>
        <td style="width:50%;padding-left:6px;">
          <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:12px;text-align:center;">
            <div style="font-size:24px;font-weight:800;color:#64748b;">{result['perdidos']}</div>
            <div style="font-size:12px;color:#64748b;">Perdidos (frios)</div>
          </div>
        </td>
      </tr></table>
    </td></tr>

    <tr><td style="padding:16px 24px 24px 24px;">
      <div style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">Pendentes para contato</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;">
        <tr style="background:#f1f5f9;">
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">Paciente</td>
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">WhatsApp</td>
          <td style="padding:8px 10px;color:#475569;font-weight:600;border-bottom:1px solid #e2e8f0;">Etapa</td>
        </tr>
        {rows}
      </table>
      <div style="font-size:11px;color:#94a3b8;margin-top:10px;">Relatório automático · enviado só para você</div>
    </td></tr>
  </table>
</div>"""
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report.py -q`
Expected: PASS. Depois `uv run pytest --tb=short -q` (zero regressão).

- [ ] **Step 5: Commit**

```bash
git add app/funnel_report.py tests/test_funnel_report.py
git commit -m "feat(funil-email): build_html_report (corpo HTML do relatório)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: `html_body` em `app/email_sender.py`

**Files:**
- Modify: `app/email_sender.py`
- Test: `tests/test_funnel_report_cron.py`

- [ ] **Step 1: APÊNDICE ao final de `tests/test_funnel_report_cron.py`:**

```python
# ── _send_email com html + send_report_email passando html_body ────────────────

def _fake_smtp(captured):
    class _FakeServer:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def login(self, *a):
            pass
        def sendmail(self, frm, to, message):
            captured["msg"] = message
    return lambda *a, **k: _FakeServer()


def test_send_email_attaches_html_when_given(monkeypatch):
    import app.email_sender as es
    captured = {}
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _fake_smtp(captured))
    es._send_email("h", 465, "u@e", "pw", "to@e", "assunto", "corpo texto",
                   html_body="<b>oi</b>")
    assert "text/plain" in captured["msg"]
    assert "text/html" in captured["msg"]


def test_send_email_plain_only_without_html(monkeypatch):
    import app.email_sender as es
    captured = {}
    monkeypatch.setattr(es.smtplib, "SMTP_SSL", _fake_smtp(captured))
    es._send_email("h", 465, "u@e", "pw", "to@e", "assunto", "corpo texto")
    assert "text/plain" in captured["msg"]
    assert "text/html" not in captured["msg"]


async def test_send_report_email_forwards_html(monkeypatch):
    import app.email_sender as es
    captured = {}

    def fake_send_email(host, port, user, password, to_email, subject, body, html_body=None):
        captured["html_body"] = html_body

    monkeypatch.setattr(es, "_send_email", fake_send_email)
    with monkeypatch.context() as m:
        m.setenv("SMTP_HOST", "h"); m.setenv("SMTP_USER", "u@e"); m.setenv("SMTP_PASSWORD", "pw")
        await es.send_report_email("s", "corpo", "dona@e", html_body="<b>x</b>")

    assert captured["html_body"] == "<b>x</b>"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report_cron.py -k "html" -q`
Expected: FAIL (o `_send_email` ainda não aceita `html_body`; `send_report_email` idem).

- [ ] **Step 3: Editar `app/email_sender.py`.**

(a) Trocar a assinatura e o corpo de `_send_email` para aceitar `html_body`:

```python
def _send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    # Ordem importa no multipart/alternative: o cliente prefere a última parte
    # suportada, então o texto vem primeiro (fallback) e o HTML por último.
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_email, msg.as_string())
```

(b) Trocar `send_report_email` para aceitar e repassar `html_body`. Assinatura:

```python
async def send_report_email(subject: str, body: str, to_email: str,
                            html_body: str | None = None) -> None:
```

E, no final da função, trocar a chamada do executor para passar `html_body`:

```python
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, _send_email, smtp_host, smtp_port, smtp_user, smtp_password,
        to_email, subject, body, html_body,
    )
```

(Não mudar mais nada em `send_report_email`; a checagem de variáveis ausentes continua igual. `send_clinic_notification_email` NÃO muda.)

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: PASS. Depois `uv run pytest --tb=short -q` (zero regressão — inclui os testes do e-mail da clínica, que devem seguir só texto).

- [ ] **Step 5: Commit**

```bash
git add app/email_sender.py tests/test_funnel_report_cron.py
git commit -m "feat(funil-email): _send_email/send_report_email aceitam html_body opcional

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Cron passa o HTML

**Files:**
- Modify: `scripts/send_funnel_report.py`
- Test: `tests/test_funnel_report_cron.py`

- [ ] **Step 1: Ajustar o teste `test_cron_builds_sets_and_sends`** em `tests/test_funnel_report_cron.py` para capturar e verificar o `html_body`. Localizar a função `fake_send` dentro desse teste e substituí-la, e ajustar a chamada e as asserções finais:

Trocar:
```python
    sent = {}
    async def fake_send(subject, body, to_email):
        sent["to"] = to_email
        sent["body"] = body
```
por:
```python
    sent = {}
    async def fake_send(subject, body, to_email, html_body=None):
        sent["to"] = to_email
        sent["body"] = body
        sent["html"] = html_body
```
E adicionar, ao final desse mesmo teste (depois das asserções existentes):
```python
    assert sent["html"] is not None and "<" in sent["html"]
    assert "5581bbb" in sent["html"]      # pendente aparece no HTML
```

Também no teste `test_cron_never_uses_clinic_email`, a `fake_send` precisa aceitar `html_body=None` (senão quebra a assinatura). Trocar a assinatura de `async def fake_send(subject, body, to_email):` para `async def fake_send(subject, body, to_email, html_body=None):` nesse teste.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_funnel_report_cron.py -k "builds_sets" -q`
Expected: FAIL (`sent["html"]` é None porque o cron ainda não gera/passa o HTML).

- [ ] **Step 3: Editar `scripts/send_funnel_report.py`.**

(a) No import de `app.funnel_report`, acrescentar `build_html_report`:
```python
from app.funnel_report import (
    compute_funnel, format_report, build_html_report,
    FUNNEL_WINDOW_DAYS, ARRIVAL_EVENT, QUALIFIED_EVENT, BOOKED_EVENT, PAID_EVENTS,
)
```

(b) Trocar o bloco final que monta e envia:
```python
    subject, body = format_report(result, now, TZ)
    print(
        f"Funil (últimos {FUNNEL_WINDOW_DAYS} dias): "
        f"interessados={result['interessados']} qualificados={result['qualificados']} "
        f"agendados={result['agendados']} pendentes={len(result['pendentes'])} "
        f"perdidos={result['perdidos']}"
    )
    await send_report_email(subject, body, to_email)
```
por:
```python
    subject, body = format_report(result, now, TZ)
    html = build_html_report(result, now, TZ)
    print(
        f"Funil (últimos {FUNNEL_WINDOW_DAYS} dias): "
        f"interessados={result['interessados']} qualificados={result['qualificados']} "
        f"agendados={result['agendados']} pendentes={len(result['pendentes'])} "
        f"perdidos={result['perdidos']}"
    )
    await send_report_email(subject, body, to_email, html_body=html)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_funnel_report_cron.py -q`
Expected: PASS. Depois `uv run pytest --tb=short -q` (zero regressão).

- [ ] **Step 5: Commit**

```bash
git add scripts/send_funnel_report.py tests/test_funnel_report_cron.py
git commit -m "feat(funil-email): cron envia o corpo HTML (texto como fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Suíte completa + verificação

**Files:** nenhum.

- [ ] **Step 1: Suíte inteira**

Run: `uv run pytest --tb=short -q`
Expected: PASS (sem regressão).

- [ ] **Step 2: Smoke import + geração de HTML de exemplo**

Run:
```bash
uv run python -c "from datetime import datetime, timezone; from zoneinfo import ZoneInfo; from app.funnel_report import build_html_report; h=build_html_report({'interessados':50,'qualificados':28,'agendados':22,'conversao_pct':44,'pendentes':[{'phone':'5581','etapa':'Qualificado','name':'Ana'}],'perdidos':21}, datetime(2026,9,21,tzinfo=timezone.utc), ZoneInfo('America/Recife')); print('ok', h.strip().startswith('<'), '44%' in h, 'Ana' in h)"
```
Expected: `ok True True True`

- [ ] **Step 3: Commit final se necessário**

```bash
git add -A && git commit -m "test(funil-email): suíte verde" || echo "nada a commitar"
```

---

## Notas de operação

- Nenhuma env ou secret novo. Nenhuma mudança de workflow. Depois de mergear e o deploy sair, na próxima rodada (ou num `workflow_dispatch` manual) o e-mail já chega em HTML.
- Depois de mergeado: `git worktree remove .worktrees/funil-email-html`.

---

## Self-review (autor do plano)

- **Cobertura do spec:** HTML aprovado (Task 1 `build_html_report`); fallback em texto mantido (`format_report` intacto, anexado primeiro no multipart — Task 2); `_send_email`/`send_report_email` com `html_body` opcional e clínica intacta (Task 2); cron gera e passa o HTML (Task 3); privacidade/agregado no log inalterados (Task 3 mantém o print agregado). Coberto.
- **Placeholders:** nenhum; todo passo tem código real.
- **Consistência de nomes:** `build_html_report`, `_bar_width_pct`, `_pendente_row`, `_ETAPA_PILL`, `html_body`, `send_report_email` usados iguais entre as tarefas; `FUNNEL_WINDOW_DAYS` já existe no módulo.
