# Design: Template de Reagendamento pelo Médico

**Data:** 2026-06-11
**Status:** Aprovado

## Contexto

Quando um médico precisa cancelar ou reagendar uma consulta, a clínica precisa notificar o paciente via WhatsApp. Este design cobre o template Meta, o script de disparo manual e o processamento da resposta pelo bot.

## Template Meta

**Nome:** `psique_reagendamento`
**Categoria:** `UTILITY`
**Idioma:** `pt_BR`
**Tipo:** texto puro (sem botões)

**Corpo:**

```
Olá, {{1}}! 👋

A consulta com {{2}} que estava agendada para *{{3}}* precisou ser remarcada.

Gostaríamos de sugerir o seguinte novo horário:
📅 *{{4}}*

O que acha? Pode nos responder aqui mesmo. 😊

— Clínica Psique
```

**Variáveis:**

| # | Conteúdo | Exemplo |
|---|---|---|
| `{{1}}` | Nome do paciente | Ana |
| `{{2}}` | Nome do médico | Dr. Júlio |
| `{{3}}` | Data/hora original | sexta, 13/06 às 10h |
| `{{4}}` | Novo horário sugerido | segunda, 16/06 às 14h |

## Script de disparo (`scripts/reschedule_notify.py`)

Script Python que a clínica executa manualmente. Recebe os seguintes argumentos:

- `--phone` — telefone do paciente (formato internacional, ex: `5511999999999`)
- `--appointment-id` — UUID do agendamento original no Supabase
- `--new-start` — novo horário sugerido em ISO 8601 (ex: `2026-06-16T14:00:00-03:00`)
- `--new-end` — fim do novo horário em ISO 8601

**Fluxo do script:**

1. Busca o appointment no Supabase para obter nome do paciente, médico e data/hora original
2. Formata as variáveis do template em português
3. Chama `send_template()` em `app/whatsapp.py` com o template `psique_reagendamento`
4. Grava `pending_reschedule` na tabela `users` do Supabase:

```json
{
  "appointment_id": "<uuid>",
  "suggested_start": "2026-06-16T14:00:00-03:00",
  "suggested_end":   "2026-06-16T15:00:00-03:00"
}
```

## Coluna `pending_reschedule` no Supabase

Adicionar coluna `pending_reschedule JSONB` na tabela `users`. Valor `null` quando não há reagendamento pendente.

## Processamento da resposta pelo bot

Quando o usuário responde após receber o template, o bot carrega `pending_reschedule` do estado e o inclui no contexto do LLM. O LLM interpreta livremente a intenção do paciente:

- **Confirmar o horário sugerido** → chama `reschedule_appointment` com `suggested_start`/`suggested_end` do `pending_reschedule`, depois limpa o campo
- **Recusar / pedir outro horário** → limpa `pending_reschedule` e entra no fluxo normal de escolha de horário
- **Dúvida ou pergunta** → responde normalmente mantendo o `pending_reschedule` ativo até o paciente decidir

O `pending_reschedule` deve ser carregado no `State` do LangGraph (similar a outros campos de contexto existentes) para que o LLM saiba que há um reagendamento pendente.

## O que não está no escopo

- Disparo automático ao bloquear agenda no Google Calendar
- Painel web para a clínica disparar o template (script CLI é suficiente por ora)
- Múltiplos horários alternativos sugeridos simultaneamente
