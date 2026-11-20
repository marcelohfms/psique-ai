# Templates para avisos que somem fora da janela de 24h

Data: 2026-11-20

## Intenção e o porquê

Quando a atendente marca uma consulta pelo painel, ou quando a Eva precisa avisar
o paciente fora de uma conversa ativa, o aviso vai como **texto livre**. O
WhatsApp (Meta) só entrega texto livre se o paciente tiver escrito nas últimas 24
horas. Fora dessa janela, a Meta descarta a mensagem **em silêncio**: ninguém
recebe e nada acusa erro.

Caso que originou isto (12/11/2026): a consulta da Nina foi marcada pela atendente
Débora pelo painel; a última mensagem real da mãe (Joane) tinha sido ~27h antes,
então a janela estava fechada e a confirmação nunca saiu. A consulta ficou
gravada, mas a família não foi avisada pela Eva.

Hoje, os avisos recorrentes que já resolveram isso (lembretes, cobrança de taxa,
cancelamento, falta, retorno, pós-consulta) usam **template aprovado da Meta**,
que é entregue mesmo fora da janela. Faltam quatro casos que ainda caem no vazio.
Este trabalho liga esses quatro casos a templates recém-criados.

## Estado atual (o que já existe)

- `app/panel_booking.py`
  - `_window_open(phone)` / `_paused_window_open(phone)`: decidem se a janela de
    24h está aberta (considera o comprovante anexado pela atendente como "não é o
    contato escrevendo", e trata conversa pausada lendo o Chatwoot).
  - `deliver_message(...)` / `send_booking_message(...)`: enviam a confirmação de
    agendamento. **Fora da janela, o destinatário vai para `not_delivered` e nada
    é enviado.** É aqui que o caso da Joane se perdeu.
  - `send_refund_message(...)`: reembolso da taxa. Fora da janela retorna
    `{"sent": False, "motivo": "janela_fechada"}` — não existe template de
    reembolso.
  - `send_payment_confirmation(...)`: **já faz o fallback de template** (dentro da
    janela manda texto, fora manda template aprovado). É o padrão a espelhar.
- `scripts/send_payment_reminders.py`
  - `_window_open(client, phone, now)`, `_send_template(...)`, `_notify(...)`:
    padrão canônico "texto dentro da janela, template fora". Os nudges importam
    daqui.
- `scripts/release_pending_reschedules.py`
  - Cron (a cada 30min, via `app/scheduler.py`). Libera a vaga de quem pediu
    remarcação e sumiu. Hoje manda `send_text` (texto livre) **sem checar a
    janela** e libera a vaga mesmo que o aviso tenha sido descartado.
- `app/chatwoot.py::send_template_message(conversation_id, template_name,
  language, category, body_params, content)`: `body_params` é um dict posicional
  `{"1": ..., "2": ...}`; `content` é o texto renderizado (vai pro log/Chatwoot).

## Templates já criados na Meta (confirmados pela Ayexa em 2026-11-20)

Categoria UTILITY, idioma `pt_BR`. Variáveis por posição, **a ordem abaixo é a que
o código tem que mandar**.

1. `consulta_agendada_taxa` — agendamento com taxa a pagar.
   `{{1}}` nome de quem recebe · `{{2}}` paciente · `{{3}}` médico · `{{4}}` data e hora.
   Texto: saudação; "A consulta de {{2}} com {{3}} ficou marcada para {{4}}.";
   taxa de R$ 100,00 em até 2 horas; PIX 42006848000178; regra de não devolução.
2. `consulta_agendada_confirmada` — agendamento sem pendência (taxa paga, isenta
   ou cortesia).
   `{{1}}` nome · `{{2}}` paciente · `{{3}}` médico · `{{4}}` data e hora.
3. `reembolso_taxa_reserva` — devolução da taxa.
   `{{1}}` nome · `{{2}}` valor devolvido · `{{3}}` paciente.
4. `vaga_liberada_remarcacao` — vaga liberada por remarcação não confirmada.
   `{{1}}` nome · `{{2}}` paciente · `{{3}}` médico.

Os textos finais aprovados estão no próprio Gerenciador da Meta. Os templates
dessa conta **não aparecem no MCP** (ver memória
`whatsapp-templates-conta-cliente-fora-do-mcp`).

## Objetivos

Ligar cada caso ao seu template, entregando template quando a janela está fechada
e mantendo o texto livre atual quando está aberta:

1. **Agendamento pelo painel** (`deliver_message`): fora da janela, enviar
   `consulta_agendada_taxa` quando `kind == "normal"`, ou
   `consulta_agendada_confirmada` quando `kind` é `taxa_paga`, `taxa_isenta` ou
   `cortesia`.
2. **Reembolso** (`send_refund_message`): fora da janela, enviar
   `reembolso_taxa_reserva` (texto). Vale **com ou sem anexo** — quando havia
   comprovante anexado, ele não é entregue fora da janela e a atendente é avisada
   disso no retorno (decisão da Ayexa, 2026-11-20).
3. **Liberação de vaga por remarcação** (`release_pending_reschedules`): adotar o
   padrão janela-aberta→texto / janela-fechada→`vaga_liberada_remarcacao`.

Em todos, quando o template for usado, manter o que o fluxo já faz hoje após o
envio: `save_message` e atualização do checkpoint do LangGraph (para a Eva saber
do que se trata quando o paciente responder).

## Não-objetivos

- **Aviso ao terceiro pagante** (`app/graph/tools.py`, `register_payment`,
  ramo `is_third_party`): hoje manda `send_text` sem checar janela, para o número
  de quem agendou (que pode estar fora da janela). Deveria reaproveitar os
  templates **que já existem** (`pagamento_taxa_recebido` /
  `pagamento_consulta_recebido`). Fica como **follow-up recomendado**, em PR
  próprio, para não misturar com os 4 templates novos e manter este PR revisável.
- Criar/editar template na Meta (feito pela Ayexa, fora do código).
- Mudar as regras de quem recebe (`consultation_reminder_contacts`), os textos
  livres existentes, ou o comportamento dentro da janela.
- Mudar o `payment_deadline_at` ajustável do painel. O template
  `consulta_agendada_taxa` usa **2 horas fixas** (decisão da Ayexa); o ajuste de
  prazo não é refletido no template.

## Anti-objetivos (o que tornaria isto um fracasso mesmo "funcionando")

- Mandar o template **dentro** da janela (romperia o tom humano de texto livre e
  gastaria template à toa).
- Liberar a vaga de uma remarcação **sem** ter conseguido avisar o paciente —
  hoje o cron só libera se o envio deu certo; isso tem que continuar valendo com o
  template (template entregue = pode liberar; falha de envio = não libera).
- Parâmetro de template com `\n`, `\t` ou espaços duplicados: a Meta rejeita.
  Cada `body_param` é uma linha única e limpa.
- Ordem de variável trocada: entregaria "marcada para Dr. Júlio" e afins.

## Decisões de design (minhas, salvo onde marcado)

- **Escolha do template de agendamento pelo `kind`** já existente em
  `_fee_fields`: `normal → consulta_agendada_taxa`; `taxa_paga | taxa_isenta |
  cortesia → consulta_agendada_confirmada`. Sem novo estado.
- **Param `{{4}}` (data e hora)**: a string "quinta-feira, 12/11/2026 às 09:00"
  (dia da semana + `%d/%m/%Y às %H:%M`), ou seja, o `format_appt_line` **sem** o
  prefixo do médico (o médico vai no `{{3}}`). Extrair um helper que produza só o
  "quando", reutilizável pelos builders.
- **`{{2}}` paciente / `{{3}}` médico**: `patient_name` (já disponível) e
  `DOCTOR_LABELS[doctor]`. Diferente do template de pagamento, que junta paciente
  e consulta num único `{{2}}` — os templates novos separam, como a Ayexa criou.
- **Agendamento dividido (1ª consulta infantil em 2 partes) fora da janela**:
  quando há 2 linhas (`len(lines) > 1`) ou `pending_part2` é True, o template de
  slot único não representa fielmente a mensagem. Decisão: **manter o
  comportamento atual** (`not_delivered`) nesses casos e deixar a atendente
  tratar; o template cobre o caso de slot único (a grande maioria). Marcar no
  retorno para a atendente saber que não foi entregue.
- **Reembolso com anexo (comprovante) fora da janela**: template não carrega
  anexo. Decisão (Ayexa, 2026-11-20): enviar o template **texto**
  (`reembolso_taxa_reserva`) assim mesmo — o paciente fica sabendo da devolução; o
  comprovante em anexo não é entregue fora da janela e isso é sinalizado no
  retorno para a atendente.
- **`content` do `send_template_message`**: passar o mesmo texto livre que já é
  montado hoje (`_text_for` / builder do reembolso / `pending_reschedule_message`),
  para o Chatwoot e o `messages` registrarem o texto renderizado.
- **Reembolso: valor `{{2}}`** (Ayexa, 2026-11-20): o valor pode variar, então a
  atendente digita no painel na hora do reembolso, com **R$ 100,00 como padrão**
  no campo, ajustando quando for diferente. Esse valor passa a ir no payload de
  `/admin/panel/refund-message` e vira o `{{2}}`, formatado "R$ 100,00". O valor
  nunca é adivinhado pelo código — sempre o que a atendente confirmou.

## Constraints

- Nada na `main`: feature nasce em worktree (`.worktrees/<branch>`), PR de lá
  (CLAUDE.md).
- Testes obrigatórios (CLAUDE.md): mockar Chatwoot/Meta. Atualizar os testes que
  dependem de `deliver_message`/`send_refund_message` retornarem `not_delivered`
  fora da janela, e adicionar testes do ramo template para os 4 casos. Arquivos
  prováveis: `tests/test_panel_booking.py` e um teste do
  `release_pending_reschedules`.
- CI verde antes do merge.

## Resolvido no builder-check (decisões do implementador)

- **Payload do endpoint de reembolso** (`/admin/panel/refund-message` em
  `dashboard/attendant_routes.py`): hoje só manda `phone`, `text` e `media`. Para
  preencher `reembolso_taxa_reserva` o payload será estendido com nome de quem
  recebe (`{{1}}`), valor (`{{2}}`) e paciente (`{{3}}`). O nome e o paciente o
  dashboard já tem na rota `reembolso`; o **valor passa a ter um campo editável no
  painel, padrão R$ 100,00** (ver Decisões de design). Mudança de interface, não de
  regra.
- **Como levar os dados até o envio do agendamento**: montar os `body_params` em
  `send_booking_message` (onde `kind`, `lines`, `pending_part2` e o `start` dos
  appointments criados estão disponíveis) e passar um "spec" de template (nome +
  params) junto do `text_for` até `deliver_message`.
- **Helper do "quando" (`{{4}}`)**: extrair de `format_appt_line` em
  `app/booking_texts.py`.
- **Cron de remarcação**: espelhar `_window_open`/`_send_template` de
  `send_payment_reminders`, mantendo o telefone do join atual e a guarda
  `manual_hold`.

## O que fica a cargo do builder

- Assinatura exata para levar `doctor`/`patient_name`/"quando" até o ponto do
  envio (hoje `deliver_message` recebe `lines` já formatadas; precisa do dado
  estruturado para montar os `body_params`).
- Onde colocar o helper do "quando" (provável: `app/booking_texts.py`, junto de
  `format_appt_line`).
- Reaproveitar `find_or_create_conversation` + `send_template_message` como em
  `send_payment_confirmation`.
- No cron de remarcação, espelhar o par `_window_open`/`_send_template` de
  `send_payment_reminders` (já é o padrão; evitar duplicar lógica de janela).

## Verificação

- `uv run pytest --tb=short` verde, incluindo os novos testes de template.
- Teste manual sugerido em staging/replay: marcar pelo painel um contato com
  janela fechada e conferir que o template chega; repetir para reembolso e para o
  cron de remarcação.
