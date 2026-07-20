# Templates de WhatsApp (Meta / Chatwoot)

Fonte única dos templates aprovados na Meta usados pela clínica. Ao criar/alterar um
template no Meta Business Manager, atualize também este arquivo.

Todos os templates são enviados via `send_template_message` em
[`app/chatwoot.py`](../app/chatwoot.py) — que só suporta variáveis no **corpo**
(`processed_params.body`), sem cabeçalho, botões ou variáveis de rodapé.

Regras de nome da Meta: minúsculas, números e `_` apenas (sem acento, espaço ou hífen).

---

## `resgate_remarcacao`

Resgate de conversa quando o paciente iniciou uma remarcação mas **não confirmou o
novo horário**. Ao iniciar a remarcação, `mark_reschedule_in_progress`
([`app/graph/tools.py`](../app/graph/tools.py)) libera o slot antigo no Google Calendar
e coloca a consulta em `pending_reschedule`. Se o paciente some, ele fica sem nada:
perde o horário antigo (voltou pra fila) e não travou o novo. Este template resgata
esse caso.

- **Categoria:** Utility (Utilidade) → subtipo **Mensagem padrão**
- **Idioma:** `pt_BR`
- **Cabeçalho / Rodapé / Botões:** nenhum
- **Uso:** disparo automático (futuro script) **ou** envio manual pela atendente.
  Por isso a mensagem fala em nome da clínica ("aqui é da Clínica Psique"), sem citar
  a Eva — serve nos dois casos.

**Corpo:**

```
Oi, {{1}}! 😊 Aqui é da Clínica Psique.

A gente começou a remarcar sua consulta com {{2}}, mas não chegou a fechar o novo horário — e não queríamos que você ficasse sem atendimento. 💙

Fica tranquilo(a): sua taxa de reserva continua garantida. Você prefere voltar pro seu horário anterior ({{3}}) ou escolher uma nova data? É só responder por aqui que a gente organiza tudo pra você. 🙌
```

**Variáveis (`body_params`):**

| Var | Conteúdo | Exemplo |
|-----|----------|---------|
| `{{1}}` | primeiro nome do paciente | `Ana` |
| `{{2}}` | médico(a) | `Dr. Júlio` |
| `{{3}}` | dia e hora do horário anterior | `quinta, 23/07 às 14h` |

---

## Templates existentes (lembretes de consulta)

Enviados por [`scripts/send_appointment_reminders.py`](../scripts/send_appointment_reminders.py).
Variáveis do corpo: `{{1}}` = primeiro nome, `{{2}}` = médico(a), `{{3}}` = horário.

| Nome | Quando | Observação |
|------|--------|------------|
| `lembrete_dia_anteior` | véspera da consulta | (nome tem typo herdado da Meta) |
| `lembrete_dia_anterior_online` | véspera, consulta online | |
| `lembrete_dia_consulta` | dia da consulta | |
| `lembrete_dia_consulta_online` | dia da consulta, online | |
