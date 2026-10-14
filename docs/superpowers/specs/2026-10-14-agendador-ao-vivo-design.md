# Agendador interno ao vivo: virada do modo sombra

Data: 2026-10-14

## Problema

As 14 rotinas da Eva (lembretes de véspera, cobrança de taxa, nudges, relatórios,
agenda do médico etc.) ainda são disparadas por cron do GitHub Actions. O GitHub
engaveta cron: o `*/10` rodava de 2 em 2 horas, às vezes com 5h de buraco, e a
mesma falha derrubou a cobrança de taxa (caso Vanessa, 08/10). Confirmações e
cobranças saíam atrasadas ou não saíam.

O PR #289 (13/10) já criou o agendador interno em `app/scheduler.py` e o pôs em
produção em **modo sombra**: ele calcula o que rodaria e grava eventos
`scheduler_shadow`, mas quem de fato executa continua sendo o GitHub. A Eva fica
ligada 24/7 no Easypanel, então é um relógio confiável.

Este spec cobre a **virada**: deixar o agendador executar de verdade e desligar os
cron do GitHub.

## Estado verificado em produção (14/10/2026)

Lido da tabela `events` (phone `agendador`), últimos 4 dias:

- 359 eventos `scheduler_shadow`, **zero** `scheduler_job_failed`. O laço está
  saudável e todas as rotinas sombreiam no ritmo certo (`appointment_reminders`
  122x, os de 30 min 42x cada).
- `doctor_daily_agenda` sombreou no slot `2026-10-13T18:00` — a pré-condição das
  18h está satisfeita. Os de manhã também (`return_reminders` 08:00,
  `no_show`/`pending_payments` 09:00).
- Último `scheduler_boot` reporta `missing_env`:
  - `funnel_report → FUNNEL_REPORT_EMAIL` — **resolvido**: a dona setou a variável
    no Easypanel em 14/10 (entra em vigor no próximo deploy, que o merge dispara).
  - `payment_reminders`, `lead_stall_nudges`, `scheduling_stall_nudges → PIX_KEY`
    — falso-positivo: os scripts usam a constante `CORRECT_PIX_KEY`
    (`app/graph/prompts.py`, via `app/booking_texts.py` e `get_pix_key`), nunca a
    env var. Precisa sair da lista de env exigida, senão o `_run_live` pula essas
    três rotinas por "missing env" — justamente a cobrança de taxa.

A Eva sobe com `uvicorn` sem `--workers`, processo único, sem risco de dois
agendadores duplicarem envio.

## Mudanças

### 1. `app/scheduler.py`

- `SCHEDULER_LIVE = False` → `True`.
- Tirar `"PIX_KEY"` do `env` de três jobs: `payment_reminders`,
  `lead_stall_nudges`, `scheduling_stall_nudges`.
- Atualizar o comentário do bloco `SCHEDULER_LIVE` e a parte da docstring que fala
  em "antes da virada" para refletir o estado ao vivo.

### 2. `.github/workflows/` (os 14 com cron)

Lista: `appointment_reminders`, `complete_appointments`, `payment_reminders`,
`release_pending_reschedules`, `lead_stall_nudges`, `scheduling_stall_nudges`,
`sync_contact_attributes`, `return_reminders`, `scheduling_stall_report`,
`funnel_report`, `send_no_show_messages`, `pending_payments_reminder`,
`doctor_daily_agenda`, `update_patient_ages`.

- Remover o bloco `schedule:` (com o `- cron:`) de cada um, mantendo
  `workflow_dispatch` (escape manual se a Eva cair).
- Nos 3 de `PIX_KEY`, apagar também a linha morta `PIX_KEY: ${{ secrets.PIX_KEY }}`
  do bloco `env`, para o teste de cobertura de segredos continuar verde.
- `block_calendar_slots`, `probe_chatwoot`, `deploy`, `test` ficam intactos (não
  têm cron).

### 3. Ação fora do código (Easypanel)

`FUNNEL_REPORT_EMAIL` já setado pela dona. `PIX_KEY` não precisa existir. Nenhuma
outra mudança de env.

## Transição (dia da virada)

No dia da virada o GitHub já disparou as rotinas da manhã. As que mandam mensagem
ao paciente (lembrete de véspera, cobrança) gravam `sent_at` e não reenviam, são
idempotentes. O risco de duplicata é só **e-mail interno** (agenda do médico,
relatório de funil, pendências).

Decisão: **mergear entre 13h e 18h (Recife), num dia que não seja dia 1º.** Nesse
horário as janelas de tolerância das rotinas de manhã (8h+4h=12h, 9h+4h=13h) já
fecharam, e o `doctor_daily_agenda` das 18h ainda dispara fresco (slot futuro, sem
claim). Zero envio repetido, sem script de seed. Hoje (14/10, quarta) serve.

## Testes

`tests/test_scheduler.py`:

- `test_jobs_cover_github_workflow_secrets` segue verde: `PIX_KEY` sai dos dois
  lados (job.env e YAML). Os workflows com só `workflow_dispatch` ainda referenciam
  `scripts/<module>.py`, então o teste continua achando-os e conferindo o resto.
- Três guardas novos, para travar o estado pretendido:
  1. `SCHEDULER_LIVE is True` — não deixa reverter a virada sem querer.
  2. Nenhum job tem `PIX_KEY` no `env` — não deixa o falso-positivo voltar.
  3. Nenhum `.github/workflows/*.yml` tem gatilho `schedule:` — a Eva é a dona das
     rotinas agora.

Rodar `uv run pytest --tb=short`, tudo verde.

## Limpeza

Apagar `scripts/_diag_scheduler_events.py` (diagnóstico temporário desta sessão).

## Rollback

Se algo quebrar depois da virada: reverter o PR volta `SCHEDULER_LIVE=False` e os
`schedule:` do GitHub, e o GitHub reassume as rotinas no próximo deploy. O
`workflow_dispatch` que fica permite disparar qualquer rotina manualmente enquanto
isso.

## Fora de escopo

- Trocar os subprocessos por chamada in-process (segue como está, isola falha).
- Mexer em qualquer script de rotina (só o disparo muda).
- `block_calendar_slots` e `probe_chatwoot` (sempre foram manuais).
