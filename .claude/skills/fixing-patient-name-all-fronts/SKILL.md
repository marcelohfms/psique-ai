---
name: fixing-patient-name-all-fronts
description: Use quando a Ayexa pedir para corrigir o nome de um paciente que ficou gravado com o nome do contato/responsável (bug do nome copiado — a Eva tratou o paciente pelo nome de quem falava, ex. a mãe) e pedir "corrigir em todas as frentes", "trocar o nome na planilha e no comprovante", "ajustar no calendar também". Cobre as quatro frentes fora do banco: planilha Pagamentos, arquivo do comprovante no Drive, eventos de log e evento do Google Calendar.
---

# Corrigindo o nome do paciente em todas as frentes

## Overview

Quando a consulta é para outra pessoa (filho, cônjuge), a Eva às vezes grava o nome de
QUEM FALA (a mãe, por exemplo) como `patient_name`, por causa da família de bug do nome do
contato copiado pro paciente ([[nome-contato-vs-paciente-cadastro]], PR #259, caso
[[caso-davi-eva-chama-de-maria-jose]]). A atendente costuma arrumar o `patients.name` pelo
painel, mas o nome errado fica congelado em vários outros lugares que o painel não toca.

**Princípio:** `patients.name` é a fonte da verdade do nome; o nome do CONTATO (o responsável)
continua sendo o dele e NÃO muda. Corrigir "em todas as frentes" = levar o nome do paciente
do `patients.name` para os quatro lugares abaixo. Sempre por script one-off, dry-run antes de
`--live`, com guardas que abortam se a linha/evento não for o esperado.

## As quatro frentes

| Frente | Onde | O que muda |
|---|---|---|
| **1. Planilha Pagamentos** | aba `Pagamentos`, coluna B (Paciente) e coluna I (Comprovante) | B = nome novo (texto puro); I = `build_receipt_filename(...)`+ext, **mantendo o hyperlink** |
| **2. Drive** | arquivo do comprovante (`drive_link` do evento `payment_receipt_registered`) | renomear com `build_receipt_filename` + `rename_file` |
| **3. Eventos de log** | tabela `events` do telefone | `metadata.patient_name` e o sufixo `— <1º nome>` em `attrs.proxima_consulta` |
| **4. Google Calendar** | evento `appointments.appointment_id` na agenda do médico | `summary` e a linha `Paciente:` da descrição |

## Diagnóstico (só leitura)

Rode scripts locais com `import scripts._dnsfix` (DNS do Supabase no Mac). Modelo pronto:
`scripts/_check_<telefone>_messages.py` já lista contato, `patient_contacts`, `patients`,
`appointments`, `events` e `messages`. Confira, nesta ordem:

1. **Qual é o nome certo:** `patients.name` (NÃO `contacts.name`, que é o responsável). Se o
   `patients.name` ainda estiver errado, a atendente tem que arrumar no painel primeiro — ou
   você arruma o `patients.name` também, mas confirme com a Ayexa.
2. **Onde o nome errado ficou:** procure o nome do responsável em `events` (metadata),
   na planilha (linha por telefone + data da consulta), no `summary`/descrição do evento do
   Calendar. Nem toda frente sempre tem o erro — corrija só as que têm.
3. **Dados da consulta:** `appointment_id`, `start_time` (UTC → Recife), `modality`, médico
   (`doctor_id` → `doctors.agenda_id`), telefone, `drive_link` da taxa.

## Correção

Copie os três templates desta pasta para `scripts/_fix_<nome>_<frente>_oneoff.py`
(`scripts/_*.py` é gitignored: fica como registro local, não commite). Preencha as constantes
com o diagnóstico. Rode cada um em dry-run, confira, depois `--live`.

- `fix_sheet_drive_template.py` — frentes 1 e 2 (planilha + Drive).
- `fix_events_template.py` — frente 3 (eventos).
- `fix_gcal_template.py` — frente 4 (Calendar).

Helpers reais (nunca reconstrua o formato à mão — ver [[payment-receipt-drive-format]]):

```python
from app.google_drive import build_receipt_filename, rename_file  # nome do arquivo
from app.google_sheets import _set_hyperlink_cell                 # coluna I com link
from app.google_calendar import _credentials                      # auth do Calendar
```

O nome do arquivo sai de `build_receipt_filename(nome, "DD/MM/AAAA", valor)` (sem extensão);
`rename_file` devolve o nome já com a extensão real do arquivo — use ESSE mesmo texto na
coluna I da planilha, senão a planilha mostra um nome que o Drive não tem.

Nos eventos, preserve o valor antigo em `metadata.manual_correction` com data e motivo.
No Calendar, troque o nome com `str.replace(OLD, NEW)` no `summary` e na descrição e
dê `events().patch` só com `summary`+`description` — o resto (médico, modalidade, número,
e-mail do responsável, nota 1ª/2ª hora) fica intacto.

## Depois

Releia as quatro frentes e prove o estado final (a planilha, o `files().get(name=...)` do
Drive, os eventos e o `events().get` do Calendar). Atualize a memória do caso como RESOLVIDO.

## Common Mistakes

| Erro | Por quê | Correto |
|---|---|---|
| Trocar o nome do contato também | o responsável continua sendo ele | só `patients.name` e as frentes do PACIENTE |
| `values().update` na coluna I | apaga o hyperlink do comprovante | `_set_hyperlink_cell` (textFormatRuns) |
| Reconstruir o nome do arquivo à mão | diverge do que o Drive/planilha têm | `build_receipt_filename` + `rename_file` |
| Renomear arquivo de outra conta no Drive | `drive.file` dá 404 fora dos arquivos do bot | só renomeia o que o bot criou ([[drive-rename-so-arquivos-do-bot]]) |
| `create_event` no lugar de `patch` | cria evento duplicado | `events().patch` no `appointment_id` existente |
| Passar UTC direto / mexer no horário | o evento já está certo, é só o nome | troque só `summary` e descrição |
| Corrigir frente que não tinha o erro | guarda aborta ou grava à toa | confira antes onde o nome errado realmente está |
| Worktree/PR pro one-off | não é código do app | script local ignorado pelo git |
