# Desligar a Eva em definitivo para um paciente

Data: 2026-09-18
Branch: feat/desligar-eva-definitivo

## Problema

Hoje não existe um jeito de silenciar de vez toda a comunicação automática da Eva
com um paciente. A atendente consegue dar alta, mas a alta só encerra o lembrete
de retorno. Os outros disparos automáticos, como lembrete de consulta, cobrança
de taxa e mensagem de falta, continuam saindo se houver o gatilho. E não há um
controle único, no painel, que a atendente aperte para dizer "essa pessoa não
recebe mais nada e a Eva não responde mais". O pedido é ter um botão que desligue
para sempre qualquer mensagem automática e também as respostas da Eva para aquele
paciente, com clareza total do estado no painel.

## Escopo

O desligamento é silêncio total. A Eva para de disparar qualquer mensagem
proativa para o paciente e também para de responder quando o paciente escreve.
Todo o contato passa a ser humano. O desligamento vale para o paciente inteiro,
ou seja, para todos os contatos ligados a ele, não só um telefone. O botão é uma
chave liga e desliga, então a atendente consegue religar a Eva depois, seja por
engano no clique, seja porque o paciente voltou.

## Decisão de mecanismo

O sistema reusa o campo `manual_hold` que já existe na tabela `contacts`, em vez
de criar uma coluna nova. O `manual_hold` já é uma pausa permanente por contato,
que não expira sozinha e só volta quando alguém reativa de propósito. O portão de
recebimento `_eva_paused_for_phone`, em `app/main.py`, já respeita o `manual_hold`
e faz a Eva não responder. Esse lado já está pronto.

A checagem feita em 2026-09-18 mostrou onze contatos em `manual_hold` no momento,
e nenhum deles tinha consulta futura marcada nem retorno ativo. Ou seja, passar os
crons a respeitar o `manual_hold` não tira nenhum lembrete legítimo de quem já está
em hold hoje. O estrago colateral é nulo no retrato atual.

## O que muda no envio proativo

O que falta é os crons de mensagem automática passarem a pular quem está em
`manual_hold`. As mensagens proativas são montadas por funções em `app/patients.py`
que recebem o `patient_id` e devolvem a lista de contatos que vão receber. As
principais são `return_reminder_contacts`, `consultation_reminder_contacts` e as
usadas pelos lembretes de pagamento e de falta, e boa parte passa por um ponto
interno comum, `_linked_contacts_with_marker`.

A mudança é: todo montador de destinatário de mensagem proativa exclui contatos
com `manual_hold` verdadeiro, do mesmo jeito que hoje já filtra por `active`. Como
o desligamento liga o `manual_hold` em todos os contatos do paciente, a lista de
destinatários fica vazia e nenhum cron manda nada. Quando a lista fica vazia por
causa disso, o cron registra no log que pulou por hold, para a auditoria não achar
que sumiu mensagem.

A verificação exata de quais funções precisam do filtro, e de que nenhuma delas é
usada num caminho ao vivo que devesse continuar enviando, é feita no plano de
implementação. O caminho ao vivo já está coberto pelo portão de recebimento, que
barra a mensagem antes de qualquer resposta, então o filtro nos montadores afeta
apenas os disparos proativos.

## O botão no painel

O controle fica no painel da atendente, na aba Paciente. Quando a Eva está ligada,
aparece um botão vermelho escrito "Desligar Eva", com uma confirmação antes de
aplicar, porque é uma ação de peso. Ao confirmar, o sistema liga o `manual_hold`
em todos os contatos daquele paciente.

Quando a Eva já está desligada, no lugar do botão aparece um selo bem visível em
duas linhas. A primeira linha traz "🔕 Eva desligada" e a segunda, menor, o
subtexto "sem lembretes e sem respostas", para deixar claro que o desligamento
cobre tudo, não só os lembretes. Ao lado do selo fica o botão "Religar Eva", que
desliga o `manual_hold` em todos os contatos do paciente e traz a Eva de volta.

O painel decide o estado olhando se algum contato do paciente está em `manual_hold`.
Se algum estiver, mostra o selo de desligada. Se nenhum estiver, mostra o botão de
desligar.

## Endpoint

Entra um endpoint novo no dashboard, no estilo dos que já existem, que recebe o
`patient_id` e um valor dizendo se é para desligar ou religar. Ao desligar, ele
grava `manual_hold` verdadeiro em todos os contatos ligados ao paciente. Ao religar,
grava `manual_hold` falso nos mesmos contatos. A camada de acesso a dados fica em
`dashboard/attendant_db.py`, junto das outras operações de contato.

## Ressalva registrada

O `manual_hold` também é ligado em contatos de representante por outro motivo, na
reconciliação de representantes. Como o religar desliga o `manual_hold` de todos os
contatos do paciente, ele pode desligar o hold de um contato que estava em hold por
essa outra razão. Na prática é o comportamento esperado, já que a atendente está
dizendo "essa pessoa volta a receber", mas fica anotado para não surpreender.

## Testes

Seguindo o CLAUDE.md, entram testes de unidade para os montadores de destinatário,
provando que um contato em `manual_hold` é pulado na lista de quem recebe lembrete.
Entra um teste do portão de recebimento confirmando que o `manual_hold` mantém a
Eva calada, caso ainda não exista. E entra um teste do endpoint novo do dashboard,
provando que desligar liga o `manual_hold` em todos os contatos do paciente e que
religar desliga em todos. Os testes ficam nos arquivos já existentes de cada camada,
e um arquivo novo só se surgir um módulo inteiramente novo.

## Fora de escopo

Não entra uma coluna nova de opt-out. Não entra um motivo ou histórico de por que a
Eva foi desligada. Não entra desligar por contato isolado dentro de um paciente, o
desligamento é sempre do paciente inteiro.
