# Agendador interno ao vivo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Virar o agendador interno de modo sombra para ao vivo e desligar os cron do GitHub, deixando a Eva (24/7 no Easypanel) dona das 14 rotinas.

**Architecture:** O código do agendador (`app/scheduler.py`) já existe e roda em sombra (`SCHEDULER_LIVE = False`). A virada é uma mudança pequena e cirúrgica: flipar a flag, tirar uma env var falso-positiva (`PIX_KEY`) de três jobs, e remover os gatilhos `schedule:` dos 14 workflows do GitHub (mantendo `workflow_dispatch` como escape manual). Três testes-guarda travam o estado novo para não regredir.

**Tech Stack:** Python 3, pytest, GitHub Actions (YAML), FastAPI (Easypanel).

Spec: `docs/superpowers/specs/2026-10-14-agendador-ao-vivo-design.md`

---

## File Structure

- `app/scheduler.py` — flip `SCHEDULER_LIVE`, remover `PIX_KEY` de 3 jobs, atualizar comentário/docstring.
- `.github/workflows/*.yml` (14 arquivos) — remover bloco `schedule:`; nos 3 de `PIX_KEY`, remover também a linha `PIX_KEY: ...`.
- `tests/test_scheduler.py` — três testes-guarda novos (append no fim do arquivo).

---

## Task 1: Virar a flag e tirar PIX_KEY dos jobs

**Files:**
- Modify: `app/scheduler.py` (linha 50 `SCHEDULER_LIVE = False`; linhas 140/144/146 `"PIX_KEY"` nas tuplas de env)
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Escrever os testes-guarda (append no fim de `tests/test_scheduler.py`)**

```python


# ── Guardas da virada (2026-10-14) ──────────────────────────────────────────────

def test_scheduler_is_live():
    """A virada: o agendador executa de verdade. Não reverter sem querer."""
    assert sch.SCHEDULER_LIVE is True


def test_no_job_requires_pix_key():
    """PIX_KEY é falso-positivo: os scripts usam a constante CORRECT_PIX_KEY.
    Se voltar ao env, o _run_live pula a cobrança de taxa por 'missing env'."""
    for job in sch.JOBS:
        assert "PIX_KEY" not in job.env, job.name
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_scheduler.py::test_scheduler_is_live tests/test_scheduler.py::test_no_job_requires_pix_key -v`
Expected: FAIL — `test_scheduler_is_live` (SCHEDULER_LIVE é False) e `test_no_job_requires_pix_key` (payment_reminders tem PIX_KEY).

- [ ] **Step 3: Flipar a flag em `app/scheduler.py`**

Trocar (por volta da linha 48-50):

```python
# Modo sombra: calcula e registra, não executa. Virar para True junto com a
# remoção dos `schedule:` dos workflows do GitHub.
SCHEDULER_LIVE = False
```

por:

```python
# Ao vivo: o agendador executa as rotinas. Os `schedule:` dos workflows do GitHub
# foram removidos na virada (2026-10-14); só o `workflow_dispatch` manual ficou.
SCHEDULER_LIVE = True
```

- [ ] **Step 4: Tirar `"PIX_KEY"` das três tuplas de env**

Em `app/scheduler.py`, dentro de `JOBS`:

`payment_reminders` — trocar:
```python
    Job("payment_reminders", "send_payment_reminders", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE + _SMTP
        + ("PIX_KEY", "CLINIC_NOTIFY_EMAIL")),
```
por:
```python
    Job("payment_reminders", "send_payment_reminders", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE + _SMTP
        + ("CLINIC_NOTIFY_EMAIL",)),
```

`lead_stall_nudges` — trocar:
```python
    Job("lead_stall_nudges", "send_lead_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE + ("PIX_KEY",)),
```
por:
```python
    Job("lead_stall_nudges", "send_lead_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE),
```

`scheduling_stall_nudges` — trocar:
```python
    Job("scheduling_stall_nudges", "send_scheduling_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE + ("PIX_KEY",)),
```
por:
```python
    Job("scheduling_stall_nudges", "send_scheduling_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE),
```

- [ ] **Step 5: Rodar e ver passar**

Run: `uv run pytest tests/test_scheduler.py::test_scheduler_is_live tests/test_scheduler.py::test_no_job_requires_pix_key -v`
Expected: PASS nos dois.

- [ ] **Step 6: Commit**

```bash
git add app/scheduler.py tests/test_scheduler.py
git commit -m "feat(agendador): vira o agendador para ao vivo e remove PIX_KEY dos jobs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Remover os cron do GitHub

**Files:**
- Modify: `.github/workflows/*.yml` (os 14 com `schedule:`)
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Escrever o terceiro guarda (append no fim de `tests/test_scheduler.py`)**

```python


def test_no_github_cron_schedules():
    """A Eva é a dona das rotinas: nenhum workflow dispara por cron.
    Só sobra o workflow_dispatch (escape manual)."""
    wf_dir = ROOT / ".github" / "workflows"
    with_schedule = [
        p.name for p in wf_dir.glob("*.yml")
        if re.search(r"^\s*schedule:\s*$", p.read_text(), re.MULTILINE)
    ]
    assert with_schedule == [], with_schedule
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_scheduler.py::test_no_github_cron_schedules -v`
Expected: FAIL — lista os 14 workflows que ainda têm `schedule:`.

- [ ] **Step 3: Remover o bloco `schedule:` dos 14 workflows**

Cada workflow tem o bloco em duas linhas (`  schedule:` seguida de `    - cron: ...`). Rodar (macOS, `sed -i ''`):

```bash
cd .github/workflows
for f in appointment_reminders complete_appointments payment_reminders \
  release_pending_reschedules lead_stall_nudges scheduling_stall_nudges \
  sync_contact_attributes return_reminders scheduling_stall_report funnel_report \
  send_no_show_messages pending_payments_reminder doctor_daily_agenda update_patient_ages; do
  sed -i '' '/^  schedule:$/,/- cron:/d' "$f.yml"
done
cd ../..
```

- [ ] **Step 4: Remover a linha morta `PIX_KEY` dos 3 workflows**

```bash
cd .github/workflows
for f in payment_reminders lead_stall_nudges scheduling_stall_nudges; do
  sed -i '' '/PIX_KEY:/d' "$f.yml"
done
cd ../..
```

- [ ] **Step 5: Conferir que cada `on:` ficou só com `workflow_dispatch` e que PIX_KEY sumiu**

Run: `grep -c "schedule:\|- cron:" .github/workflows/*.yml | grep -v ":0" || echo "nenhum cron restante"`
Expected: `nenhum cron restante`

Run: `grep -rn "PIX_KEY" .github/workflows/`
Expected: nenhuma linha.

Run (sanidade, bloco `on:` do primeiro arquivo):
`sed -n '3,6p' .github/workflows/appointment_reminders.yml`
Expected: `on:` seguido de `  workflow_dispatch:` e linha em branco (sem `schedule:`/`cron:`).

- [ ] **Step 6: Rodar os testes do agendador (inclui a cobertura de segredos)**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS em tudo, incluindo `test_no_github_cron_schedules` e `test_jobs_cover_github_workflow_secrets` (PIX_KEY saiu dos dois lados).

- [ ] **Step 7: Commit**

```bash
git add .github/workflows tests/test_scheduler.py
git commit -m "feat(agendador): remove os cron do GitHub; a Eva assume as rotinas

Mantém workflow_dispatch em cada workflow como escape manual.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Atualizar a docstring do scheduler

**Files:**
- Modify: `app/scheduler.py` (docstring, linhas ~24-27)

- [ ] **Step 1: Atualizar o trecho da docstring que fala em "antes da virada"**

Trocar (por volta das linhas 24-27):

```python
SCHEDULER_LIVE=False é o modo sombra: o agendador calcula e registra o que rodaria
(evento scheduler_shadow) mas não executa nada, enquanto o GitHub continua sendo
o dono das rotinas. Serve para conferir horários e variáveis de ambiente em
produção antes da virada.
```

por:

```python
SCHEDULER_LIVE controla a execução: True (padrão desde a virada de 2026-10-14) a
Eva executa as rotinas; False é o modo sombra, que só calcula e registra o que
rodaria (evento scheduler_shadow) sem executar — foi como rodou antes da virada,
com o GitHub ainda dono. O modo sombra fica disponível para uma próxima mudança
grande que queira reconferir horários e variáveis em produção antes de ligar.
```

- [ ] **Step 2: Rodar os testes do agendador (garante que nada quebrou)**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add app/scheduler.py
git commit -m "docs(agendador): docstring reflete o estado ao vivo

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Suíte completa e PR

**Files:** nenhum (verificação e entrega)

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: tudo verde. (Ver memória "Pytest: ordem dos arquivos e import circular" se houver erro de import — não passar `test_patients.py` como 1º arg.)

- [ ] **Step 2: Conferir o diff do PR**

Run: `git log origin/main..HEAD --oneline` e `git diff origin/main..HEAD --stat`
Expected: 4 commits (spec + 3 desta execução); arquivos tocados = `app/scheduler.py`, 14 workflows, `tests/test_scheduler.py`, os 2 docs.

- [ ] **Step 3: Push e abrir o PR**

```bash
git push -u origin scheduler-ao-vivo
gh pr create --title "feat(agendador): vira o agendador para ao vivo e desliga os cron do GitHub" --body "$(cat <<'EOF'
## O quê

Vira o agendador interno (`app/scheduler.py`, PR #289) de modo sombra para ao vivo e remove os gatilhos `schedule:` dos 14 workflows do GitHub. A Eva (24/7 no Easypanel) passa a ser a dona das rotinas; o GitHub só engavetava cron (rodava de 2-5h em 2-5h).

## Mudanças

- `SCHEDULER_LIVE = True`.
- `PIX_KEY` sai do `env` de `payment_reminders`, `lead_stall_nudges`, `scheduling_stall_nudges` (falso-positivo: os scripts usam a constante `CORRECT_PIX_KEY`; mantê-lo faria o `_run_live` pular a cobrança de taxa por "missing env").
- Removido o `schedule:` dos 14 workflows; `workflow_dispatch` fica como escape manual. `PIX_KEY: ${{ secrets.PIX_KEY }}` morto removido dos 3.
- Três testes-guarda: `SCHEDULER_LIVE is True`, nenhum job com `PIX_KEY`, nenhum workflow com `schedule:`.

## Pré-condições (feitas)

- `FUNNEL_REPORT_EMAIL` setado no Easypanel (entra em vigor neste deploy).
- Shadow das 18h do `doctor_daily_agenda` confirmado em produção; zero `scheduler_job_failed` em 4 dias.

## Transição

Mergear entre 13h e 18h (Recife), fora do dia 1º, para não repetir e-mail interno (as mensagens a paciente são idempotentes via `sent_at`).

## Rollback

Reverter o PR volta `SCHEDULER_LIVE=False` e os `schedule:`; o GitHub reassume no próximo deploy.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Limpeza do script de diagnóstico (checkout principal)**

O `scripts/_diag_scheduler_events.py` foi criado só no checkout principal (não está na worktree nem no PR). Remover:

Run: `rm /Users/ayexatavares/Projetos/psique-ai/scripts/_diag_scheduler_events.py`

---

## Pós-merge (ação da dona / manual)

- Confirmar o deploy no Easypanel e `psiqueai.ayexa.com.br/health` = ok.
- Conferir no banco, no dia seguinte, um `scheduler_run` ou `scheduler_job_failed` por rotina de horário fixo (sinal de que o agendador executou ao vivo). Zero `scheduler_shadow` novo.
- `git worktree remove .worktrees/scheduler-ao-vivo` depois do merge.
