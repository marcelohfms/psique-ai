# Data de retorno visível e editável no painel da atendente

**Data:** 2026-08-12

## Problema

A "data de retorno" de um paciente (a data em que a médica pediu que ele volte)
hoje mora na tabela `return_reminders` (`next_return_date`) e só é gravada pela
página `/retornos` do dashboard — usada pelos médicos para classificar consultas.

O painel da atendente embutido no Chatwoot (`dashboard/templates/atendente.html`)
**não mostra** essa data. Consequências:

- A atendente não sabe, durante o atendimento, o que a médica informou de retorno.
- Quando o paciente pede para mudar (ex.: médica pediu 2 meses, paciente só pode
  no 3º mês), ninguém atualiza `next_return_date`. Confirmado no código: a Eva (bot
  em `app/`) **nunca** lê nem escreve `return_reminders` — ao reagendar, ela só mexe
  em `appointments`. O cron `scripts/send_return_reminders.py` continuaria disparando
  o lembrete na data antiga.

## Objetivo

Exibir a data de retorno no painel da atendente, no formato **"Data de retorno:
XX/XX/XX"**, e permitir que a atendente **altere** essa data conforme o combinado
com o paciente. Ao salvar, o lembrete automático passa a se realinhar à nova data.

## Escopo

Incluído:
- Exibir `next_return_date` no bloco Paciente do painel `atendente.html`.
- Campo de data editável para alterar `next_return_date`.
- Ao salvar, **zerar as flags de envio** (`month_before_sent_at`,
  `month_of_sent_at`, `overdue_sent_at`) para o cron re-agendar os lembretes na nova
  data.

Fora de escopo (decisões tomadas no brainstorming):
- **Não** permitir criar retorno do zero para paciente sem linha em
  `return_reminders`. O campo só edita retornos que a médica já classificou. Sem
  linha → campo vazio e somente leitura. (Evita mexer no schema / no CHECK de
  `return_interval`.)
- **Não** sincronizar automaticamente o reagendamento feito pela Eva com
  `return_reminders` (lacuna conhecida, mas é outro trabalho).
- Nenhuma mudança na página `/retornos` (dos médicos) nem no bot `app/`.

## Desenho

### Camada de dados — `dashboard/attendant_db.py`

1. **Leitura.** Nova função `get_return_reminder(patient_id) -> dict | None` que faz
   `SELECT next_return_date, return_interval, doctor_id FROM return_reminders
   WHERE patient_id = ...` e devolve a linha (ou `None`). Espelha o padrão das outras
   funções `get_*` do arquivo.

2. **Escrita.** Nova função `update_return_reminder(patient_id, data)`:
   - Whitelist própria `_RETURN_FIELDS = {"next_return_date"}` + `_filter`.
   - **Só faz UPDATE** (não upsert): `UPDATE return_reminders SET
     next_return_date = ..., month_before_sent_at = NULL, month_of_sent_at = NULL,
     overdue_sent_at = NULL, updated_at = now() WHERE patient_id = ...`.
   - Se não houver linha para o `patient_id`, o UPDATE não afeta nada (comportamento
     coerente com "só editar existentes"). A função retorna se atualizou ou não.

### Rotas — `dashboard/attendant_routes.py`

3. **Leitura.** A rota `GET /api/atendente/paciente/{pid}` passa a incluir no JSON de
   resposta a chave `return_reminder` (resultado de `get_return_reminder`), junto de
   `patient` e `link`.

4. **Escrita.** Nova rota `POST /api/atendente/paciente/{pid}/retorno` que recebe
   `{phone, data: {next_return_date}}` e chama `update_return_reminder`. Mesma
   autenticação por token da query string usada pelas demais rotas do painel.

### Front — `dashboard/templates/atendente.html`

5. No `renderForms`, dentro da `<section>` do bloco Paciente, adicionar um
   sub-bloco destacado **"Data de retorno"**, posicionado **abaixo dos campos e dos
   checkboxes** e **antes** do botão "Salvar paciente" (visual aprovado no mockup
   `docs/.../mockup_data_retorno.html` / brainstorming de 2026-08-12):
   - Se `return_reminder` existir: input `type="date"` preenchido com
     `next_return_date`, um botão próprio **"Salvar retorno"** (rotina `saveRetorno`,
     separado do "Salvar paciente" porque grava em outra tabela e realinha lembretes),
     e uma nota curta com o que a médica informou — ex.: "informado pela médica:
     retorno em 2 meses" (derivada de `return_interval`).
   - Se não existir: exibir "Data de retorno: —" somente leitura (sem input),
     com dica "(nenhum retorno classificado ainda)".
   - Destaque visual leve (borda/fundo em tom de acento) para a atendente notar a
     informação assim que abre a aba.

   Mapa de `return_interval` → nota amigável (para a nota "informado pela médica"):
   `15_dias`→"retorno em 15 dias", `1_mes`→"1 mês", `2_meses`→"2 meses",
   `3_meses`→"3 meses", `4_meses`→"4 meses", `6_meses`→"6 meses",
   `alta`→"paciente recebeu alta". Para `alta`, exibir a nota mas manter o campo de
   data vazio/somente leitura (não há próxima data).

## Fluxo

```
Atendente abre o painel no Chatwoot
  → GET /api/atendente/paciente/{pid}
      → get_patient + get_link + get_return_reminder
  → painel mostra "Data de retorno: 15/09/26" (editável)

Paciente pede para mudar para outubro
  → atendente edita a data → saveRetorno
  → POST /api/atendente/paciente/{pid}/retorno {next_return_date}
      → update_return_reminder: grava nova data + zera flags de envio
  → cron send_return_reminders volta a avaliar e dispara na nova data
```

## Tratamento de erros

- UPDATE sem linha correspondente: não é erro; a rota responde ok informando que
  nada foi alterado (o front pode manter o estado somente leitura).
- Data inválida / vazia enviada pelo front: validar no back (formato ISO `YYYY-MM-DD`)
  e rejeitar com 400, sem gravar.
- Sem token válido: mesma resposta de auth das outras rotas do painel.

## Testes

Em `dashboard/tests/` (espelhando `test_main_retornos.py` / `test_return_reminders.py`),
com Supabase mockado:

- `get_return_reminder` devolve a linha quando existe e `None` quando não existe.
- `GET /api/atendente/paciente/{pid}` inclui `return_reminder` no payload.
- `update_return_reminder` grava a nova `next_return_date` **e** zera as três flags de
  envio.
- UPDATE em paciente sem linha não cria linha nova e sinaliza "nada alterado".
- Rota de update valida data inválida (400) e exige token.

## Arquivos afetados

- `dashboard/attendant_db.py` — `get_return_reminder`, `update_return_reminder`,
  `_RETURN_FIELDS`.
- `dashboard/attendant_routes.py` — incluir `return_reminder` na leitura; nova rota
  de update.
- `dashboard/templates/atendente.html` — linha "Data de retorno" (exibição + edição).
- `dashboard/tests/` — novos casos de teste.

Sem migration. Sem alteração em `app/` nem na página `/retornos`.
