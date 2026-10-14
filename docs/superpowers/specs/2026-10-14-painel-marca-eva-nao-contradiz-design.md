# Painel marca a consulta, a Eva não pode contradizer

## Problema

Quando a atendente marca uma consulta pelo painel no meio de uma conversa em que a
Eva está em pleno fluxo de agendamento (acabou de oferecer horários e de dizer que
não há vaga para o dia pedido), a Eva, nos turnos seguintes, reabre o agendamento,
reoferta horários e nega a vaga que a clínica acabou de confirmar. O paciente lê
isso como "não há nada para mim" e desiste de uma consulta válida e, às vezes, já
paga.

### Caso real (Dario Gleison Leite De Marcelos, 5581998196655, 14/10/2026)

1. O paciente pediu consulta para hoje. A Eva disse que não havia vaga hoje nem nos
   próximos dias e ofereceu só datas de novembro (05, 09, 11, 12/11), com a Dra. Bruna.
2. Às 09:44 (horário de Recife) a atendente marcou pelo painel uma consulta com a
   Dra. Bruna para hoje às 14:00, presencial. Saiu pela Eva a mensagem "Consulta
   registrada ✅ 14/10 às 14:00" e o pedido da taxa de reserva de R$ 100.
3. O paciente mandou o comprovante de R$ 100. A Eva respondeu que o horário das 14:00
   "não ficou disponível para registrar agora" e reofertou novembro.
4. Mesmo depois de registrar o pagamento, a Eva continuou pedindo que o paciente
   escolhesse um horário de novembro "para eu registrar a consulta".
5. O paciente escreveu "só fiz o agendamento porque a consulta seria hoje; não sendo
   hoje não tenho interesse" e a Eva transferiu para um atendente.

A consulta das 14:00 de hoje existia, estava ativa e com a taxa paga. A Eva desfez
isso na cabeça do paciente.

## Causa raiz

O agendamento pelo painel (`app/panel_booking.py`, `deliver_message`) faz três coisas
no checkpoint do LangGraph: envia a mensagem de "Consulta registrada", anexa essa
mensagem ao histórico e zera o `pending_appointment`. Mais nada.

Ele não neutraliza o resto do contexto de agendamento que a Eva carregava (as ofertas
de novembro e o "não há vaga hoje" que ela mesma escreveu minutos antes) e não avisa
o próximo turno que o agendamento já está resolvido. O `patient_agent_node` não tem
nenhuma consciência de que o painel acabou de marcar uma consulta — ele só vê o
histórico, onde a Eva estava oferecendo novembro.

No turno seguinte, o histórico de ofertas pesa mais do que a linha "Consultas
agendadas: 14/10 às 14:00" que o prompt injeta (`app/graph/nodes.py`, bloco
"Inject upcoming/recent appointments", ~linha 2832). A Eva reabre o agendamento e
contradiz a consulta confirmada.

Esta é a mesma classe de corrida painel-versus-Eva de casos anteriores
(`painel-atendente-vs-eva-pagamento`, `caso-marcelo-remarcacao-manual-sem-aviso`),
mas aqui a Eva não só deixou de avisar: ela ativamente desfez o que a clínica fez.

## Escopo

Cirúrgico. A trava só liga depois de uma marcação recente feita pelo painel. Não é
uma regra geral do tipo "nunca ofereça horário se já existe consulta ativa" — essa
versão abrangente foi descartada no brainstorming por risco de atrapalhar quem quer
remarcar ou marcar uma segunda consulta.

## Abordagem (A): prevenção no prompt + rede de segurança pós-resposta

Ataca a contradição nos dois pontos onde ela se manifesta, seguindo o padrão de
guards e injeção de diretiva que já existe no `patient_agent_node`. Nenhum ponto
único de falha: se a injeção no prompt não segurar (a Eva já ignorou a injeção de
consultas agendadas uma vez), o guard pós-resposta corrige.

### 1. Detecção do gatilho — `_recent_panel_booking(phone, now)`

Helper assíncrono novo em `app/graph/nodes.py`, junto dos outros helpers de guard
(perto de `_last_slot_offer_is_stale`). Devolve os dados da consulta marcada pelo
painel (um dict com pelo menos `appointment_id` e a `datetime` local formatada para a
diretiva/confirmação) quando **todas** as condições abaixo valem; caso contrário,
devolve `None`.

1. **Marcação recente pelo painel.** O evento `appointment_booked` mais recente do
   telefone com `metadata.origem == "painel"` foi criado há menos de
   `PANEL_BOOKING_GUARD_MAX_AGE` (constante nova = `timedelta(hours=6)`, folgada para
   cobrir uma conversa inteira com vai e volta de pagamento).
2. **Mais recente que a última oferta.** O `created_at` desse evento de painel é mais
   recente que o `created_at` do último evento `slots_offered` do telefone, ou não há
   `slots_offered`. É isto que desliga o gatilho sozinho: quando a Eva fizer uma busca
   nova e legítima depois (remarcação de verdade), `slots_offered` passa a ser mais
   recente e o gatilho para de disparar.
3. **Consulta ainda ativa.** A consulta apontada pelo `appointment_id` do evento ainda
   está com `status == "scheduled"` no banco.

Fontes: `get_events_by_type(phone, "appointment_booked", ...)` e
`get_events_by_type(phone, "slots_offered", limit=1)` (ambos já existem e devolvem
mais recente primeiro); a checagem de status lê a tabela `appointments` por
`appointment_id`. Erro em qualquer leitura = devolve `None` (não arrisca travar uma
conversa por falha de I/O).

Observação: `get_events_by_type` com `origem == "painel"` — como o filtro é por
`metadata`, o helper busca os eventos `appointment_booked` recentes e filtra
`origem` em Python (o primeiro que casar já é o mais recente, pela ordem decrescente).

### 2. Prevenção no prompt

No `patient_agent_node`, no mesmo ponto onde entram as consultas agendadas
(logo após o bloco `if upcoming:` que monta `future_lines`), quando
`_recent_panel_booking` casa, acrescentar ao `system_prompt` um bloco de diretiva
forte, com conteúdo equivalente a:

> ATENÇÃO: a clínica acabou de marcar pelo painel a consulta de `<DD/MM às HH:MM>`.
> Essa consulta está CONFIRMADA. NÃO ofereça outros horários, NÃO diga que não há
> vaga, NÃO reabra o agendamento. Trate essa consulta como a consulta ativa. Se o
> paciente enviar comprovante, registre o pagamento normalmente. Se o paciente quiser
> cancelar ou remarcar, siga o fluxo normal.

A diretiva fica depois da lista de consultas agendadas para ter alta saliência.

### 3. Rede de segurança pós-resposta — `GUARD_PANEL_BOOKED`

Depois que a Eva gera o texto final (ramo `if not response.tool_calls and
response.content`), perto do `GUARD_STALE_SLOTS` (~linha 3399), quando:

- `_recent_panel_booking` ainda casa neste turno, **e**
- o texto da resposta `_looks_like_slot_offer(...)` (detector que já existe) **ou**
  nega disponibilidade,

então descartar o texto da Eva e enviar no lugar uma confirmação curta e determinística
da consulta real marcada pelo painel (usando a `datetime` do helper), encerrando o
turno — mesmo padrão de "envia direto e termina o turno" do `GUARD_SLOT_TAKEN_VERBATIM`
(envia via `send_text`, grava com `save_message`, devolve a `AIMessage` e
`pending_appointment: None`).

Como o gatilho já é estreito (painel marcou recentemente, mais novo que a última
oferta, consulta ainda ativa), trocar o texto aqui é seguro: dentro dessa janela,
uma oferta de horário ou negativa de vaga é quase sempre a contradição que queremos
eliminar.

Detecção de "nega disponibilidade": manter conservador. Reaproveitar
`_looks_like_slot_offer` para o caso das ofertas múltiplas e adicionar uma checagem
simples de frases de negativa de vaga ("não temos horário", "sem horário",
"não ficou disponível", "não há vaga" e variações sem acento) combinada com contexto
de horário/data. Como aqui o guard **substitui** (não re-verifica na agenda como o
GUARD_FALSE_NO_SHIFT), a checagem fica deliberadamente estreita para não trocar uma
mensagem legítima; o gatilho já limita o risco.

## Testes

Seguindo a estrutura de um arquivo por camada:

- **`tests/test_process_message.py`** (ou o arquivo onde vivem os testes do
  `patient_agent_node`): testes de unidade para `_recent_panel_booking`, mockando
  `get_events_by_type` e a leitura de `appointments`, cobrindo cada condição ligada e
  desligada:
  - casa quando painel recente + mais novo que `slots_offered` + consulta `scheduled`;
  - **não** casa quando há `slots_offered` mais novo que a marcação do painel (gatilho
    autodesliga);
  - não casa quando a marcação do painel é mais antiga que `PANEL_BOOKING_GUARD_MAX_AGE`;
  - não casa quando a consulta não está mais `scheduled`;
  - não casa quando não há evento de painel;
  - devolve `None` em erro de leitura.
- Testes de integração mockados reproduzindo o caso do Dario: com um painel-booking
  recente no estado, a resposta da Eva que ofereceria novembro é substituída pela
  confirmação da consulta das 14:00 (GUARD_PANEL_BOOKED); e, com a diretiva injetada,
  o prompt do turno contém a instrução de não reabrir agendamento.

Todos os testes devem passar no CI (`.github/workflows/test.yml`).

## Fora de escopo

- A regra geral "nunca ofereça horário se já existe consulta ativa" (descartada).
- Mudar o que o painel grava no checkpoint em `deliver_message` — a correção fica no
  lado do `patient_agent_node`, que é quem tem o contexto do turno. (Se no plano de
  implementação ficar mais limpo o painel registrar um marcador dedicado em vez de a
  gente inferir pelo evento `appointment_booked`, isso é detalhe de implementação a
  decidir no plano, mas o default é inferir pelo evento que já existe.)
