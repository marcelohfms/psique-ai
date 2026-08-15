# Lembrete e cancelamento por não-pagamento fora da janela de 24h via template

**Data:** 2026-08-14
**Arquivo afetado:** `scripts/send_payment_reminders.py`
**Branch:** `fix/payment-cron-template`

## Problema

O cron `send_payment_reminders.py` envia o lembrete da taxa de reserva e o aviso de
cancelamento por não-pagamento sempre como **mensagem livre**, via
`send_whatsapp` → `app.whatsapp.send_text` (Chatwoot). Fora da janela de 24h do
WhatsApp (Meta), mensagem livre não é entregável: o Meta descarta, mas o Chatwoot
cria a mensagem e **não lança exceção**. Resultado:

- O guard `any_notified` de `_cancel_unpaid_appointment` ("só cancela se pelo menos
  um contato foi notificado") vira falsa proteção — acha que avisou e cancela.
- O paciente não recebe nem o lembrete nem o aviso de cancelamento, e pode
  aparecer na clínica achando que tem consulta.

Auditoria (14/08/2026): 56 cancelamentos por não-pagamento em 90 dias, **todos**
com a janela de 24h fechada no momento do cancelamento; 7 deles com consulta
futura e nada ativo no lugar.

Padrão já existente no projeto: outros crons que enviam fora da janela
(`send_appointment_reminders.py`, `send_no_show_messages.py`,
`send_return_reminders.py`, `complete_appointments.py`) usam
`app.chatwoot.send_template_message` com **templates aprovados no Meta**.

## Objetivo

Fazer o lembrete e o cancelamento serem entregáveis fora da janela, usando
template quando a janela estiver fechada e mantendo o texto livre atual quando
estiver aberta (envio híbrido). Com isso, o guard `any_notified` volta a
significar entrega de verdade e o cancelamento silencioso deixa de ocorrer.

## Escopo

**Dentro:**
- Detecção da janela de 24h por contato.
- Envio híbrido (texto livre dentro da janela; template fora) para lembrete e
  cancelamento.
- Ajuste do guard de cancelamento para depender do sucesso real do envio.
- Testes.

**Fora:**
- Os 7 pacientes já afetados (a clínica tratou manualmente após revisar as
  conversas). Este ajuste vale daqui pra frente.
- Confirmação de entrega via webhook de status do Meta/Chatwoot (delivered/read).
  Não é necessária: escolher o canal entregável (template fora da janela) já
  corrige a causa raiz. YAGNI.
- Wording dos textos livres atuais (`payment_reminder_message`,
  `payment_cancel_message`) — permanecem como estão.

## Dependência externa (bloqueia a ativação, não o merge)

Dois templates **UTILITY / pt_BR** precisam ser criados e aprovados pela clínica
no Meta (via Chatwoot) antes de o caminho fora-da-janela funcionar:

| Template (nome fixo no código) | Params (posicionais) |
|---|---|
| `taxa_reserva_lembrete` | {{1}} contato · {{2}} referência da consulta · {{3}} médico · {{4}} data/hora |
| `taxa_reserva_cancelamento` | {{1}} contato · {{2}} referência da consulta · {{3}} médico · {{4}} data/hora |

`{{2}}` (referência da consulta) codifica o caso próprio-paciente vs. responsável,
espelhando o texto livre atual — **não** é o nome cru do paciente:

| Situação | `{{2}}` no lembrete | `{{2}}` no cancelamento |
|---|---|---|
| contato **é** o paciente (adulto p/ si) | `sua consulta` | `da sua consulta` |
| contato é responsável | `a consulta de Bento` | `da consulta de Bento` |

O `_notify` monta `{{2}}` a partir de `contact["name"] == patient_name` (mesma
condição que hoje decide `patient_first` nos builders de texto livre).

- PIX e valor (R$ 100,00) ficam **fixos no corpo** do template (não são params).
- O corpo aprovado deve espelhar o texto de `payment_reminder_message` /
  `payment_cancel_message` para não haver dois wordings divergentes.
- O texto livre (`content=`) enviado junto ao template serve de registro legível
  no Chatwoot e espelha o mesmo conteúdo.

### Corpo dos templates (o que a clínica cadastra no Meta)

Categoria **UTILITY**, idioma **Português (BR) / pt_BR**. Sem cabeçalho, sem
botões, sem rodapé (só o corpo). Os `{{n}}` são as variáveis na ordem
contato · paciente · médico · data-hora.

**`taxa_reserva_lembrete`** — corpo:

```
Olá, {{1}}! 😊 Só passando para lembrar que {{2}} com {{3}} no dia {{4}} ainda aguarda o pagamento da taxa de reserva de R$ 100,00.

💳 PIX: 42006848000178

Assim que o pagamento for realizado, a vaga estará garantida! Se tiver qualquer dúvida sobre o pagamento, é só chamar aqui. 🙏
```

Exemplos p/ aprovação — {{1}} Mariana · {{2}} a consulta de Bento · {{3}} Dr. Júlio · {{4}} 27/08/2026 às 14:00
(caso próprio-paciente: {{1}} João · {{2}} sua consulta · {{3}} Dr. Júlio · {{4}} 27/08/2026 às 14:00)

**`taxa_reserva_cancelamento`** — corpo:

```
Olá, {{1}}. Infelizmente, como não recebemos o pagamento da taxa de reserva {{2}} com {{3}} no dia {{4}} dentro do prazo, precisamos liberar a vaga. 😔

Caso queira reagendar, é só chamar aqui! Ficaremos felizes em atender você. 💙
```

Exemplos p/ aprovação — {{1}} Mariana · {{2}} da consulta de Bento · {{3}} Dr. Júlio · {{4}} 27/08/2026 às 14:00
(caso próprio-paciente: {{1}} João · {{2}} da sua consulta · {{3}} Dr. Júlio · {{4}} 27/08/2026 às 14:00)

Notas de compatibilidade com WhatsApp/Meta:
- Nenhuma variável no início/fim do corpo e nunca duas seguidas (ok nos dois).
- O texto livre atual usa `*negrito*` em nome/médico/data; no template os `{{n}}`
  entram sem asteriscos (formatação em template com variável costuma ser
  rejeitada). O texto livre dentro da janela permanece com o negrito de hoje.
- O template de cancelamento usa "dentro do prazo" (sem "de 4 horas") para não
  cravar um número que hoje diverge do intervalo real; o texto livre dentro da
  janela permanece como está.

Enquanto os templates não existem: dentro da janela tudo funciona (texto livre);
fora da janela o envio de template falha → `_notify` retorna `False` → o guard
**adia** o lembrete/cancelamento (comportamento seguro, sem cancelamento
silencioso). Nenhuma regressão frente ao estado atual.

## Design

### 1. Detecção da janela — `_window_open`

```python
async def _window_open(client, phone: str, now: datetime) -> bool:
    """True se o contato mandou alguma mensagem (role='user') nas últimas 24h.

    A janela de atendimento do WhatsApp é por conversa/contato. Fora dela, só
    template aprovado é entregável.
    """
```

- Fonte: tabela `messages`, `role='user'`, maior `created_at` do contato.
- Normalização de telefone: reutilizar a mesma lógica de fork do 9º dígito já
  usada por `find_receipt_in_conversation` (contatos e mensagens divergem no 9º
  dígito). Extrair um helper compartilhado se hoje estiver inline, para os dois
  caminhos usarem exatamente a mesma normalização.
- `now` é injetado (não `datetime.now()` interno) para testabilidade.
- Sem inbound → `False` (fechada).

### 2. Envio híbrido — `_notify`

```python
async def _notify(client, phone, contact_first, patient_first,
                  doctor_label, date_str, kind, now) -> bool:
    """Envia lembrete (kind='reminder') ou cancelamento (kind='cancel').
    Dentro da janela: texto livre. Fora: template. Retorna sucesso do envio."""
```

- `kind='reminder'`: texto de `payment_reminder_message`; template
  `taxa_reserva_lembrete`.
- `kind='cancel'`: texto de `payment_cancel_message`; template
  `taxa_reserva_cancelamento`.
- Janela aberta → `send_text(phone, texto_livre)`.
- Janela fechada → `send_template_message(conv_id, template_name,
  language="pt_BR", category="UTILITY", body_params={"1":contato, "2":paciente,
  "3":médico, "4":data}, content=texto_livre)`.
- `{{2}}` (referência da consulta) é montado por `_notify` a partir de
  `contact["name"] == patient_name` — "sua consulta"/"da sua consulta" quando o
  contato é o próprio paciente, "a consulta de X"/"da consulta de X" quando é
  responsável (ver tabela na seção de templates). Mesma condição que os builders
  de texto livre já usam para `patient_first`.
- Retorna `True` se o envio (livre ou template) não lançou exceção; `False` caso
  contrário. Erros são logados como hoje.
- O `save_to_checkpoint` existente continua sendo chamado nos dois caminhos.

### 3. Integração nos dois fluxos

- `_send_payment_reminder`: substitui o bloco `send_whatsapp(...)` por
  `_notify(..., kind="reminder")`; `any_sent` passa a ser o OR dos retornos.
  Só grava `payment_reminder_sent_at` se `any_sent`.
- `_cancel_unpaid_appointment`: substitui `send_whatsapp(...)` por
  `_notify(..., kind="cancel")`; `any_notified` passa a ser o OR dos retornos.
  O guard "só cancela se `any_notified`" permanece — agora com significado real.
  As guardas de comprovante (`find_receipt_in_conversation`) e cortesia
  (`_is_courtesy`) continuam antes, inalteradas.

### 4. Testes (`tests/`)

Arquivo: `tests/test_payment_reminders.py` (novo — módulo próprio do cron; segue a
regra do CLAUDE.md de um arquivo por camada/módulo).

- `_window_open`: inbound há 1h → True; há 30h → False; sem inbound → False;
  inbound sob telefone com/sem 9º dígito casa (fork).
- `_notify` roteamento: janela aberta → chama `send_text`, não chama template;
  janela fechada → chama `send_template_message` com `template_name` e
  `body_params` corretos por `kind`.
- `_notify` referência da consulta `{{2}}`: contato == paciente → "sua consulta"
  (lembrete) / "da sua consulta" (cancel); contato responsável → "a consulta de
  X" / "da consulta de X". Verificado tanto no `body_params` do template quanto
  no texto livre.
- `_notify` retorno: envio ok → True; exceção no envio → False.
- Guard de cancelamento: `_notify` retorna False (template indisponível) →
  appointment **não** é cancelado (status intacto, sem `cancel_calendar_event`);
  retorna True → cancela normalmente.

Todos com mocks de Supabase / Chatwoot / Google Calendar (sem rede), como os
testes existentes.

## Riscos e mitigações

- **Template não aprovado ainda:** fora da janela o envio falha e o guard adia —
  seguro. Combinar com a clínica a aprovação antes de considerar "resolvido".
- **Nome/param divergente do que a clínica cadastrar:** nomes e ordem dos params
  ficam documentados aqui e fixos no código; a clínica cadastra igual.
- **Falso "janela aberta" por mensagem inbound antiga mal-datada:** improvável;
  `created_at` vem do webhook. Threshold estrito de 24h.
