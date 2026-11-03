# Caixa de reembolso no financeiro do painel

Data: 2026-11-01
Autor: sessão Claude (brainstorm com a Ayexa)
Status: proposto, aguardando aprovação

## Intenção e o porquê

Quando um paciente cancela uma consulta cuja taxa de reserva de R$100 já foi paga
e opta por receber o dinheiro de volta, hoje o reembolso vira um beco sem saída no
painel. O pedido é registrado (a Eva ou o painel carimbam `refund_requested_at` e
escrevem uma linha "Solicitação de Reembolso" na planilha Solicitações), mas a
baixa não existe em lugar nenhum do painel. Não há como anexar o comprovante do
estorno nem lançar a saída de R$100 na planilha Pagamentos. A única forma de fechar
é pela Eva (`confirm_refund_completed`), que depende de a atendente devolver a
conversa com nota privada, coisa que quase nunca acontece porque a Eva costuma estar
pausada nesses casos. Resultado: o caixa mostra dinheiro recebido e nunca devolvido,
e cada estorno precisa de conserto manual (foi o que acabou de acontecer no caso
Tarso, corrigido à mão em 01/11/2026).

A ideia é transformar esse reembolso pendente numa "caixa" de verdade no financeiro
do paciente, igual à caixa de consulta, mas com valor negativo. A atendente fecha
essa caixa anexando o comprovante do estorno (marcado como reembolso) e, ao fechar,
a planilha Pagamentos recebe a linha de -100 automaticamente. Opcionalmente, o
painel envia a mensagem ao paciente com o comprovante, substituindo o envio manual.

## Como funciona hoje (estado atual)

- O financeiro do painel monta "caixas" a partir de `compute_pendencias`
  (`dashboard/payments.py`), que só olha agendamentos com status `scheduled` ou
  `completed` e gera caixas de "taxa" e "consulta" não pagas. A atendente quita cada
  uma com `mark_paid`: sobe o comprovante no Drive (`upload_comprovante`), grava no
  agendamento, lança a linha na planilha Pagamentos (`_append_payment_sheet`) e
  espelha o comprovante na tabela `messages`. Hoje `mark_paid` **não** envia WhatsApp
  ao paciente; só manda e-mail à clínica e espelha internamente.
- Cancelamento com devolução: `build_cancel` + `commit_cancel`
  (`app/panel_appointments.py`) com `fee_action="devolver"` muda o status para
  `canceled` e chama `_register_refund`, que carimba `refund_requested_at` e escreve
  a linha "Solicitação de Reembolso" na planilha Solicitações. Fim. Não há baixa.
- Eva: `register_refund_request` faz o equivalente ao `_register_refund`.
  `confirm_refund_completed` é o único ponto que fecha: carimba `refund_completed_at`,
  lança a linha -100 "Reembolso - Taxa de Reserva" na planilha Pagamentos
  (`append_refund_request` em `app/google_sheets.py`), grava evento `refund_completed`
  e manda mensagem ao paciente pela Eva.
- O app do painel (`dashboard/`) **não importa `app/`**; helpers compartilhados são
  espelhados (ex.: `_format_person_name`). Qualquer lógica de planilha/Drive/Chatwoot
  usada pela caixa de reembolso precisa existir (ou ser espelhada) dentro de `dashboard/`.
- **Envio de mensagem ao paciente já existe, mas só texto.** O painel manda texto ao
  paciente hoje via `_send_via_eva` (`dashboard/attendant_routes.py`), que chama o
  endpoint `/admin/panel/payment-confirmation` da Eva (`send_payment_confirmation` em
  `app/panel_booking.py`). Esse caminho entrega texto dentro da janela de 24h e valida
  o template contra uma lista fixa (`pagamento_taxa_recebido`, `pagamento_consulta_recebido`,
  `isencao_taxa_reserva`); fora da janela sem template ele devolve `sent=False`. Já
  **envio de mídia/anexo ao paciente não existe em lugar nenhum do código** —
  `app/chatwoot.py` só tem `send_message` (texto), `send_template_message` e
  `add_private_note`. Anexar o comprovante dentro do WhatsApp é capacidade inédita.

## O que será construído

### 1. A caixa de reembolso no financeiro do paciente

Uma nova caixa aparece no financeiro do paciente (painel da atendente) quando existe
um reembolso pendente, definido como: agendamento com `status = "canceled"`,
`refund_requested_at` preenchido e `refund_completed_at` nulo. Essa condição cobre os
dois caminhos de origem (Eva e painel), porque ambos já carimbam `refund_requested_at`.

Aparência: caixa no mesmo formato da caixa de consulta, porém destacada como saída —
valor **-R$100** (negativo), rótulo "Reembolso - Taxa de reserva", com visual que
deixe claro que é dinheiro saindo (ex.: cor/realce negativo). Mostra paciente, médico
e a data da consulta cancelada, como as outras caixas.

Fonte de dados: uma consulta separada no `dashboard/payments.py` (as pendências de
reembolso não cabem em `compute_pendencias`, que filtra `scheduled`/`completed`). Um
`compute_reembolsos_pendentes(client, patient_ids)` análogo, devolvendo as caixas de
reembolso do(s) paciente(s).

### 2. A baixa do reembolso

Ação da atendente no painel, análoga ao "marcar como pago" da consulta. Ao dar baixa:

1. **Comprovante (opcional, não trava):** a atendente pode anexar o comprovante do
   estorno. Se anexar, ele sobe na pasta de Pagamentos do Drive com nome que deixa
   explícito que é reembolso (variante de `build_receipt_filename` com marcação
   "Reembolso"), e vira o hyperlink na célula de comprovante da planilha. Se não
   anexar, a baixa acontece do mesmo jeito.
2. **Banco:** carimba `refund_completed_at` e `updated_at` no agendamento.
3. **Planilha Pagamentos:** lança a linha de saída -100 "Reembolso - Taxa de Reserva"
   (mesmo formato de `append_refund_request`), com o comprovante no hyperlink quando
   houver.
4. **Evento:** grava `refund_completed` (mesmo evento que a Eva grava hoje), com
   `registered_via: "dashboard"`, valor e `appointment_id`.
5. **Espelho:** espelha o comprovante/baixa na tabela `messages`, no mesmo padrão do
   `mark_paid` atual, para auditoria e simetria.

### 3. A mensagem ao paciente (opcional, escolha da atendente)

Na hora da baixa, a atendente escolhe se o painel envia ou não a mensagem ao paciente.
Essa mensagem substitui o envio manual que a atendente faria.

- **Não enviar:** só registra a baixa; a atendente avisa o paciente por conta própria.
- **Enviar, com comprovante anexado:** manda a mensagem citando o comprovante e anexa
  o arquivo. Rascunho (tom da clínica, sem botões; a clínica ajusta):
  > "Olá, {primeiro_nome}! 💜 Segue o comprovante do seu reembolso da taxa de
  > reserva. Quando quiser agendar a próxima consulta, estamos à disposição!"
- **Enviar, sem comprovante:** texto adaptado, sem citar anexo:
  > "Olá, {primeiro_nome}! 💜 Seu reembolso da taxa de reserva foi realizado. Quando
  > quiser agendar a próxima consulta, estamos à disposição!"

Destinatário: o contato que agendou/pagou (`appointments.contact_id`), que é quem
recebe o dinheiro de volta — não necessariamente o paciente (ex.: caso Tarso, a mãe
Amanda). O `{primeiro_nome}` vem do nome desse contato. (Decisão minha; mesma regra de
destino que `compute_pendencias` já usa para a confirmação de pagamento.)

Envio: a parte de **texto** reaproveita o caminho que já existe (`_send_via_eva` →
`send_payment_confirmation`), que entrega dentro da janela de 24h. Decisão minha:
reusar esse padrão em vez de abrir um caminho paralelo de texto. O **anexo do
comprovante entra na v1** (decisão da Ayexa, 01/11/2026), então esta entrega inclui
construir o envio de mídia ao paciente (ver seção 4).

Comportamento da mensagem com comprovante: envia o texto que cita o comprovante e
anexa o arquivo. Se o anexo não puder ser entregue (janela de 24h fechada ou erro no
envio), a baixa é registrada mesmo assim e o painel avisa a atendente que a mensagem
com o comprovante não saiu e ela precisa mandar na mão — nunca manda o texto "segue o
comprovante" sem o arquivo ter ido junto.

## Restrições

- **Janela de 24h do WhatsApp.** Mensagem livre e mídia só são entregues dentro da
  janela de 24h desde a última mensagem do paciente. Um estorno pode sair dias depois
  do contato. Fora da janela, só um template aprovado entrega, e template com mídia +
  texto livre é mais complexo. Decisão padrão (minha): o painel tenta enviar pela
  conversa; se o envio falhar (janela fechada ou erro), a baixa é registrada mesmo
  assim e o painel avisa a atendente que a mensagem não saiu e ela precisa mandar na
  mão. É candidata a decisão da Ayexa se a clínica quiser um template garantido.
- **`dashboard/` não importa `app/`.** Variante do nome de arquivo, linha de reembolso
  na planilha e envio via Chatwoot precisam existir/ser espelhados dentro de `dashboard/`.
- **Sem mensagem duplicada.** Quando o painel envia a mensagem, ela substitui o envio
  manual. A Eva não deve mandar uma confirmação por cima (a Eva costuma estar pausada
  nesses casos; o caminho `confirm_refund_completed` da Eva é separado e permanece como
  está para o caso raro de o paciente confirmar direto com a Eva).
- **Idempotência.** Dar baixa num reembolso já com `refund_completed_at` não deve
  duplicar linha na planilha nem reenviar mensagem.

### 4. Envio de mídia ao paciente (capacidade nova)

Como o anexo entra na v1, esta entrega constrói o envio de imagem/PDF ao paciente pelo
WhatsApp, que hoje não existe. O caminho deve seguir o mesmo modelo do texto: o
`dashboard/` chama um endpoint da Eva (como já faz com `payment-confirmation`), e a
plumbing de mídia fica em `app/` (Chatwoot/Meta), não duplicada no painel. Em linhas
gerais: uma função de envio de anexo em `app/chatwoot.py` (hoje só texto/template/nota),
usando a API de mensagens do Chatwoot com `attachments[]`, e um endpoint no painel da
Eva que recebe o pedido do dashboard e dispara texto + anexo.

A atendente anexa o arquivo uma vez só na baixa; o mesmo arquivo vai para o Drive (como
comprovante de reembolso) e para o WhatsApp do paciente. Só entrega dentro da janela de
24h; fora dela, cai no aviso descrito na seção 3.

## Decisões já tomadas (minhas, do builder-check)

- **Primeira consulta de menor dividida em duas linhas.** O cancelamento com devolver
  exige cancelar as duas partes, mas só uma linha carrega `refund_requested_at` (a
  caixa já aparece uma vez só por isso). Na baixa: carimbar `refund_completed_at` na
  linha que tem `refund_requested_at` (a mesma), lançar **uma** linha de -100 na
  planilha e gerar **uma** caixa. Não carimbar as duas nem lançar duas linhas — isso
  reabriria a classe de bug de cobrança/registro em dobro de consultas divididas.
- **Caminho da mensagem de texto.** Reusar `_send_via_eva`/`send_payment_confirmation`,
  não criar um endpoint paralelo. Dentro da janela, texto livre entrega sem template
  novo; fora da janela, o endpoint devolve `sent=False` e o painel avisa a atendente —
  que é exatamente a decisão padrão deste desenho.
- **Linha da planilha.** Não é só o espelho de `append_refund_request` (que não faz
  hyperlink nem estica a Tabela nativa). A baixa combina o formato dessa linha de -100
  com a lógica de `_set_hyperlink_cell`/`_extend_table_to_row` do `_append_payment_sheet`,
  para o comprovante virar hyperlink quando houver. (Builder resolve ao escrever.)

## Objetivos

- Reembolso vira uma caixa negativa de -100 no financeiro do paciente, fechável pela
  atendente.
- A baixa preenche a planilha Pagamentos corretamente (linha de saída -100), com ou
  sem comprovante.
- A atendente anexa o comprovante quando tiver, destacado como reembolso.
- A atendente escolhe enviar ou não a mensagem ao paciente; o texto se adapta a ter ou
  não comprovante.
- Acaba o conserto manual de estorno (tipo o caso Tarso).

## Não-objetivos

- Reembolso de valor de consulta (parcial ou integral). O escopo é só a taxa de
  reserva de R$100.
- Reembolsos de cancelamentos sem taxa paga.
- O caminho de crédito (`fee_action="credito"`, `pending_reschedule`), que não é
  devolução de dinheiro.
- Mudar a UI do dashboard geral `/pagamentos` (a caixa é no financeiro do paciente).

## Anti-objetivos (o que seria fracasso mesmo "funcionando")

- Mensagem dizendo "segue o comprovante" sem anexo nenhum.
- Mensagem automática ao paciente quando a atendente escolheu não enviar.
- Dupla mensagem (painel + Eva) sobre o mesmo estorno.
- Linha de -100 duplicada na planilha por baixa repetida.
- Caixa de reembolso sumir antes de a baixa ser dada, ou continuar aparecendo depois.

## O que fica a cargo do builder

- Onde exatamente encaixar a caixa no template do financeiro do paciente e o CSS do
  realce negativo.
- A forma concreta de enviar texto + anexo ao paciente (seção 4): assinatura da função
  de mídia em `app/chatwoot.py`, como a imagem/PDF chega até ela (bytes vindos do
  dashboard no momento da baixa), o endpoint da Eva que o dashboard chama, e como
  detectar falha de envio (janela fechada / erro) para avisar a atendente.
- Nome canônico do arquivo de reembolso no Drive (seguir o padrão de
  `build_receipt_filename`, acrescentando a marcação de reembolso).
- Rota/handler no `dashboard/attendant_routes.py` e o estado do formulário (anexo
  opcional + toggle enviar mensagem).
- Testes: espelhar a cobertura de `mark_paid`/cancelamento — unidade para a montagem da
  caixa e a decisão de texto da mensagem; integração mockada para Supabase, planilha,
  Drive e Chatwoot. Seguir a estrutura de `dashboard/tests/` e `tests/`.

## Trabalho posterior já implícito (fora deste escopo)

- Unificar a baixa do painel e a `confirm_refund_completed` da Eva numa lógica só, se
  valer a pena, para não manter dois pontos que escrevem a mesma linha.
