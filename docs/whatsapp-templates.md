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

---

## Templates de retorno periódico

Enviados por [`scripts/send_return_reminders.py`](../scripts/send_return_reminders.py),
1x/dia, conforme a classificação feita pelo médico no dashboard `/retornos`
(ver [`dashboard/return_reminders.py`](../dashboard/return_reminders.py)).

Categoria: **Utility (Utilidade)**. Idioma: `pt_BR`. Sem cabeçalho/rodapé/botões.

**Precisam ser criados e aprovados no Meta Business Manager antes do cron
conseguir enviá-los.**

### Variante `_terceiro`

Cada um dos 3 lembretes tem duas versões: a normal (contato = próprio
paciente) e uma variante `_terceiro` (contato ≠ paciente, ex: mãe agendando
pelo filho) — igual ao padrão já usado para `lembrete_dia_consulta` /
`lembrete_dia_consulta_online`. Texto de template aprovado é fixo, então não
dá pra ter uma frase condicional ("seu retorno" vs "o retorno de Fulano")
dentro do mesmo template — precisa dos dois.

- **Normal** (`{{1}}` = primeiro nome do contato, `{{2}}` = médico(a)): fala
  "seu retorno"/"você".
- **`_terceiro`** (`{{1}}` = primeiro nome do contato, `{{2}}` = primeiro
  nome do paciente, `{{3}}` = médico(a)): fala "o retorno de {{2}}"/"{{2}}".

**Repetição de variável:** a Meta não permite reusar o número de uma
variável dentro do mesmo template. Sempre que o nome do paciente aparece de
novo no corpo de um `_terceiro`, vira uma variável NOVA e sequencial
(`{{4}}`, `{{5}}`...) — sempre com o mesmo valor de `{{2}}`. Ver
`scripts/send_return_reminders.py::_build_body_params` e
`_TERCEIRO_PATIENT_EXTRA_VARS` para o mapeamento exato de cada template.

`scripts/send_return_reminders.py::_send_for_row` escolhe a variante
comparando o nome do contato com o nome do paciente (mesma lógica de
`send_payment_reminders.py::payment_reminder_message`).

**Nomes:** `retorno_um_mes_antes` e `retorno_um_mes_antes_terceiro` (nomes
antigos) não podem ser reusados — foram criados e excluídos por engano no
Meta Business Manager, que bloqueia reuso do nome por 30 dias. Os templates
correspondentes usam `retorno_mes_anterior`/`retorno_mes_anterior_terceiro`.

### `retorno_mes_anterior`

Disparado quando o mês atual é o mês anterior ao de `next_return_date`. Nunca
disparado para `return_interval = 15_dias` ou `1_mes` (ver
`dashboard/return_reminders.py::save_classification`).

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Passando para avisar que seu retorno com {{2}} está previsto para o mês que vem.

Manter a regularidade das consultas é fundamental para o acompanhamento do seu tratamento, especialmente considerando que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código de Ética Médica. Assim você evita ficar sem acesso à medicação quando chegar a hora.

Se quiser já deixar reservado um horário, é só nos avisar por aqui! 😉
```

### `retorno_mes_anterior_terceiro`

`{{1}}` = contato, `{{2}}` = paciente, `{{3}}` = médico(a), `{{4}}` = paciente (repetição).

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Passando para avisar que o retorno de {{2}} com {{3}} está previsto para o mês que vem.

Manter a regularidade das consultas é fundamental para o acompanhamento do tratamento, especialmente considerando que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código de Ética Médica. Assim {{4}} evita ficar sem acesso à medicação quando chegar a hora.

Se quiser já deixar reservado um horário, é só nos avisar por aqui. 😉
```

### `retorno_no_mes`

Disparado quando o mês atual é o mesmo mês de `next_return_date`.

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Verificamos que você está no período indicado para a sua próxima consulta com {{2}}, gostaria de agendar?

Manter a regularidade das consultas é fundamental para o acompanhamento do seu tratamento. Além disso, a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então agendar em dia é importante para que você não fique sem acesso à medicação.

Estamos à disposição para agendar o horário que melhor se encaixa para você! 😉
```

### `retorno_no_mes_terceiro`

`{{1}}` = contato, `{{2}}` = paciente, `{{3}}` = médico(a), `{{4}}` = paciente (repetição).

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Verificamos que {{2}} está no período indicado para a próxima consulta com {{3}}, gostaria de agendar?

Manter a regularidade das consultas é fundamental para o acompanhamento do tratamento. Além disso, a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então agendar em dia é importante para que {{4}} não fique sem acesso à medicação.

Estamos à disposição para agendar o horário que melhor se encaixa! 😉
```

### `retorno_atrasado`

Disparado uma única vez quando o mês atual é posterior ao mês de
`next_return_date` (sem repetição mensal).

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Notamos que o período indicado para o seu retorno com {{2}} já passou. Como o acompanhamento regular é importante para a continuidade do seu tratamento, ficamos à disposição para remarcar o quanto antes.

Vale lembrar também que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então quanto antes retomarmos as consultas, menor o risco de você ficar sem acesso à medicação.

Se puder nos responder com sua disponibilidade, já organizamos um horário para você. 😉
```

### `retorno_atrasado_terceiro`

`{{1}}` = contato, `{{2}}` = paciente, `{{3}}` = médico(a), `{{4}}` e `{{5}}` = paciente (repetições).

```
Olá, {{1}}! 😊 Aqui é a Eva, secretária virtual da Psiquê. 

Notamos que o período indicado para o retorno de {{2}} com {{3}} já passou. Como o acompanhamento regular é importante para a continuidade do tratamento, ficamos à disposição para remarcar o quanto antes.

Vale lembrar também que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então quanto antes retomarmos as consultas, menor o risco de {{4}} ficar sem acesso à medicação.

Se puder nos responder com sua disponibilidade, já organizamos um horário para {{5}}. 😉
```
