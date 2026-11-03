# Prazo da taxa de reserva ajustável pelo painel da atendente

Data: 2026-11-01

## Intenção e o porquê

Hoje, quando a Eva registra uma consulta, a taxa de reserva tem que ser paga
em até 2 horas. O cron `scripts/send_payment_reminders.py` manda o lembrete e,
se não houver pagamento, cancela a consulta sozinho. Alguns pacientes não
conseguem pagar no mesmo dia. Vão receber um dinheiro numa data próxima, ainda
antes da consulta, e querem pagar aí. A atendente não tem como segurar esse
caso pelo painel: ou a taxa é paga a tempo, ou a consulta cai.

O caso que motivou isto foi a Camila (mãe do Eduardo). A consulta foi
registrada dia 30/10 às 17:35, com prazo de 2 horas, e ela estava só esperando
o link de pagamento.

A ideia é dar à atendente humana um controle no painel para adiar, manualmente,
a data limite de pagamento da taxa de reserva, desde que essa data caia antes
do dia da consulta.

## O que já existe e vamos reusar

A coluna `appointments.payment_deadline_at` já existe e já muda o
comportamento do cron. Em `scripts/send_payment_reminders.py` (função que
decide se já é hora de cobrar):

```
deadline = appt.get("payment_deadline_at")
if deadline:
    limit = start_time - 24h
    return min(deadline, limit) <= now
return created_at <= now - 2h
```

Ou seja: com `payment_deadline_at` preenchida, o cron só começa a cobrar a
partir dessa data (nunca depois de 24h antes da consulta), e o cancelamento
segue 2h depois do lembrete, como sempre. A Eva já escreve nessa coluna pela
ferramenta `extend_payment_deadline` (em `app/graph/tools.py`), que trava o
prazo em no máximo 24h antes da consulta. Essa ferramenta da Eva **não muda**
neste trabalho.

O que falta é expor esse ajuste para a atendente, no painel, com a regra de
limite que a clínica quer (véspera da consulta, não 24h).

## O comportamento

Na aba **Pagamentos** do painel, cada pendência de taxa (card com `tipo:
"taxa"`, onde já ficam os botões "Isentar taxa", "pagar com comprovante" e
"Não compareceu") ganha um controle novo: a atendente escolhe uma data e
confirma. Ao confirmar, o sistema grava essa data em `payment_deadline_at` de
todos os `appointment_id` daquela pendência (a pendência pode juntar mais de
um id, caso da 1ª consulta dividida em duas partes) e zera
`payment_reminder_sent_at`, para um lembrete já enviado não disparar o
cancelamento na hora seguinte. A partir daí o cron só volta a cobrar naquela
data.

A confirmação é só no banco. **Não** manda mensagem para o paciente (diferente
de "isentar", que avisa). É um ajuste operacional interno: a atendente já
combinou o prazo com o paciente na conversa.

### Regra do limite

A atendente escolhe uma data (dia). Essa data vale como "o paciente tem até o
fim desse dia para pagar": gravo o prazo às **23:59 de Recife** do dia
escolhido. Com isso, o cron só volta a cobrar na manhã seguinte ao dia
escolhido, e o paciente tem o dia inteiro para pagar.

A data escolhida tem que ser:

- de hoje em diante (não pode ser no passado), e
- o fim desse dia (23:59) tem que cair **antes do horário da consulta**.

Na prática isso deixa a atendente escolher hoje mesmo, quando o paciente vai
pagar ainda hoje para uma consulta de outro dia, e qualquer dia até a véspera.
Só não dá para segurar uma consulta que é hoje. Fora da regra, o painel recusa
com uma mensagem clara.

Observação sobre o backstop de 24h: o cron mantém o `min(deadline,
start_time - 24h)` que ele já tem hoje. Então, se a atendente marcar a véspera
e a consulta for de manhã cedo, o lembrete pode acabar saindo um pouco antes do
que ela marcou (puxado para 24h antes), e aí o paciente não pega o dia inteiro
da véspera. Esse backstop é uma proteção que já existe e vale para a Eva
também; este trabalho **não** mexe no cron.

## Onde mexe

Tudo no painel da atendente (`dashboard/`), mais o template.

1. **`dashboard/payments.py`** — função nova para gravar o prazo:
   recebe os `appointment_id` (separados por vírgula, como as outras rotas de
   pagamento já tratam) e a data escolhida. Busca no banco o `start_time` de
   cada id e usa o **mais cedo** (na pendência agrupada vale a parte que começa
   primeiro). Valida a regra do limite contra esse `start_time`. Grava
   `payment_deadline_at` e zera `payment_reminder_sent_at` em cada id. Registra
   um evento (`attendant_payment_deadline_set`) com a data antiga e a nova.

2. **`dashboard/attendant_routes.py`** — rota nova
   `POST /api/atendente/pagamentos/{appointment_id}/prazo`, no mesmo padrão de
   `isentar`: valida o token, valida o escopo do appointment, chama a função de
   `payments.py`. Body só com `phone` e `data_limite` (data no formato
   `YYYY-MM-DD`). O `start_time` da consulta **não** vem do navegador: o
   servidor busca no banco (segurança — senão daria para mandar um horário
   falso e furar a regra de "antes da consulta").

   Escopo com id agrupado (1ª consulta dividida, ids separados por vírgula): a
   validação **separa por vírgula e valida cada id**. A rota `isentar` hoje
   passa o valor agrupado cru para `_assert_appointment_scope`, que faz um
   `.eq` exato e devolve 403 nesse caso — ou seja, há um bug latente em
   `isentar` para a pendência agrupada. A rota nova não copia esse padrão: usa
   uma validação que trata os ids separados. (Ver "Decisões que tomei" sobre
   corrigir o `isentar` junto.)

3. **`dashboard/templates/atendente.html`** — no card de taxa, um botão
   "Ajustar prazo da taxa" que abre um seletor de data e, ao confirmar, chama a
   rota nova e atualiza a lista.

4. **Correção do `isentar`** — no mesmo ponto de escopo, fazer o `isentar`
   tratar a pendência agrupada (separar os ids por vírgula), para parar de dar
   403 na 1ª consulta dividida.

5. **Testes** (`dashboard/tests/`) — na mesma estrutura dos testes que já
   existem para pagamento/isenção. Cobrir: grava a data (23:59 de Recife) e
   zera o lembrete; permite hoje quando a consulta é de outro dia; recusa data
   no passado; recusa quando o fim do dia escolhido cai depois do horário da
   consulta; trata vários ids (pendência agrupada); e a correção do escopo do
   `isentar` na pendência agrupada. Mock de Supabase, como os outros testes da
   pasta.

## Fora de escopo (não-metas)

- Não muda a ferramenta `extend_payment_deadline` da Eva nem a regra de 24h
  dela.
- Não muda o cron `send_payment_reminders.py`.
- Não manda mensagem ao paciente ao ajustar o prazo.
- Não cria isenção nem pagamento; é só o prazo.
- A correção do `isentar` é só o tratamento do id agrupado no escopo; nada mais
  da rota de isenção muda.

## Decisões já fechadas

- **Permitir hoje.** A data pode ser de hoje em diante; o que importa é o fim
  do dia escolhido cair antes da consulta (decisão sua).
- **Corrigir o `isentar` junto.** O bug de escopo na pendência agrupada entra
  nesta mesma entrega (decisão sua).
- **Hora do dia gravada.** A atendente escolhe só a data (dia); gravo às 23:59
  de Recife, pra dar o dia inteiro ao paciente e o cron só voltar a cobrar na
  manhã seguinte.
- **`start_time` vem do banco.** O servidor busca o horário da consulta no
  banco (a parte mais cedo, na pendência agrupada); o navegador não manda esse
  dado.
- **Só a atendente.** O controle fica no painel da atendente (autenticado por
  token), então por construção só a atendente humana usa.

## Decisões que tomei (confirmar na revisão)

- **Rótulo e texto do botão.** Proponho "Ajustar prazo da taxa" e, ao abrir,
  explicar em uma linha: "O paciente tem até o fim deste dia para pagar a taxa.
  Antes disso, a consulta não é cancelada por falta de pagamento." Posso trocar
  as palavras.
