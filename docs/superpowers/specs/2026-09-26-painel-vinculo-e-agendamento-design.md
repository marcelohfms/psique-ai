# Painel da Eva: vínculo de contato e gestão de consultas

Data: 26/09/2026. Status: desenho e visual aprovados na conversa. Protótipo navegável: https://claude.ai/artifact/1PVtfTdPd6rggiR8MLVzH8 (privado).

## Objetivo

Hoje o painel da atendente (`/atendente`, embutido no Chatwoot) só mostra os pacientes já ligados ao número da conversa e permite editar o vínculo existente. Ele não procura outro paciente, não cria vínculo, não cria ficha e não mexe em consultas, a não ser para registrar pagamento, falta e isenção. Tudo que envolve agendar, remarcar ou cancelar depende de pedir à Eva por nota privada, o que falha justamente nos encaixes fora da grade.

Este projeto dá ao painel o ciclo completo: vincular o número a um paciente, criar ficha, desvincular, agendar (inclusive encaixe e primeira consulta infantil dividida), isentar por consulta, remarcar e cancelar, sempre avisando o paciente.

## Arquitetura

O painel (`dashboard/`) é um app separado e não importa `app/`. A divisão fica assim.

O vínculo (parte 1) é só banco de dados e fica inteiro no painel, em `dashboard/attendant_db.py` e `dashboard/attendant_routes.py`.

As consultas (partes 2 e 3) passam por endereços internos novos na Eva, no mesmo padrão dos `/admin/*` que já existem em `app/main.py`, protegidos pelo cabeçalho `X-Admin-Secret` (`_check_admin_secret`). O painel coleta os dados, mostra a prévia e chama a Eva. A Eva reaproveita o código que já usa em `confirm_appointment`, `reschedule_appointment`, `cancel_appointment` e `register_refund_request` (`app/graph/tools.py`). A lógica central dessas ferramentas será extraída para funções que não dependem do estado do grafo, usadas tanto pela ferramenta quanto pelo endereço novo, sem duplicar regra. O painel ganha duas variáveis de ambiente: `EVA_BASE_URL` e `ADMIN_SECRET`.

O motivo de a Eva fazer esse trabalho, e não o painel, é que a mensagem enviada ao paciente precisa entrar na memória da conversa (checkpoint do LangGraph). Assim, quando o paciente responder "paguei" ou mandar comprovante, a Eva sabe do que se trata.

## Interface

O painel vira uma página só, sem trocar de tela. O protótipo navegável é a referência visual. A paleta é a do site da Psiquê (`psique-site/styles.css`): fundo creme `#FAF6F0`, linhas em areia `#EADFD0`, texto grafite `#42424A` e cinza `#6E6D77`, e a lavanda `#575684` (com `#3F3E63` no hover) nos botões principais, na aba ativa, nos interruptores ligados e no "+". A tipografia usa uma serifa editorial (Newsreader) para nomes e datas e uma sans discreta (Geist) para o resto. Fora a lavanda, a cor só aparece com significado: azul só no encaixe, âmbar só em prazo ou pendência, vermelho só no gesto irreversível, verde na situação paga (aba Financeiro) e na Eva ligada. Ícones são desenhados (traço fino), nunca emoji. Toda escolha entre opções é um controle segmentado único, e ligar ou desligar é um interruptor.

O topo aparece em todas as abas e é centrado no número da conversa, porque um número pode ter vários pacientes. Em destaque fica o nome do contato, com o telefone embaixo e um lápis pequeno. O lápis abre a edição de nome e CPF do contato; o CPF não aparece antes disso. Ao lado fica o indicador de Eva ativa. Logo abaixo fica a caixa de paciente: mostra o paciente selecionado e de quem é o número ("Lucas Menezes · número da mãe"). Ao clicar, lista os pacientes ligados ao número, cada um com um ícone de desvincular. Ao lado da caixa, um botão "+" abre o vínculo. Tudo abaixo do topo mostra os dados do paciente selecionado.

As abas passam a ser Consultas (nova, abre primeiro), Financeiro e Cadastro (antiga aba Paciente), com Resetar afastado à direita. A aba Contato deixa de existir: nome e CPF foram para o lápis do topo e o vínculo foi para a caixa de paciente.

A aba Consultas começa pelo paciente em destaque (nome, etiqueta Infantil quando houver, idade, médico e quantidade de consultas) e o botão "Nova consulta". Abaixo, as consultas futuras numa linha do tempo vertical: data grande à esquerda, um ponto lavanda na linha e um cartão com hora, médico, modalidade, etiquetas discretas (1ª consulta, parte 1 de 2, encaixe, acompanhamento) e dois ícones, alterar e cancelar. A aba Consultas não mostra pagamento: situação de taxa e de consulta fica só na aba Financeiro. A pendência de 2ª parte sem horário aparece tracejada no fim da linha, com o botão "Marcar".

Nova consulta, alterar, cancelar e vincular abrem numa folha lateral à direita, que não esconde a lista. O rodapé da folha tem o destinatário da mensagem ("Carla · mãe") com a prévia do texto sob demanda e o botão principal. No encaixe, o primeiro clique mostra o aviso azul com o motivo e troca o botão por "Confirmar encaixe", em azul; só o segundo clique grava. No cancelar, o botão principal é vermelho.

A aba Financeiro mantém as funções de hoje com o visual novo: cada pendência é um cartão com a etiqueta do tipo (taxa de reserva ou consulta), valor, forma de pagamento em controle segmentado (PIX, crédito, débito, dinheiro), anexar comprovante e "Marcar pago", com "Não compareceu" e "Isentar taxa" (esta só na taxa) discretos. Abaixo, o responsável financeiro em modo leitura (CPF parcialmente escondido) com lápis para editar. A aba Cadastro reúne a ficha (dados, médico, modalidade, interruptores de retornante, exceção de idade e taxa sempre isenta, e preço especial), o retorno e o interruptor "Eva para este paciente", que deixa o cartão rosado quando desligado. A aba Resetar ganha confirmação em dois passos, que hoje não existe.

## Parte 1: vínculo de contato a paciente

### Marcação "próprio paciente"

A regra da idade (`consultation_reminder_contacts` e `return_reminder_contacts` em `app/patients.py`) só considera um número como "próprio" quando `is_self` é verdadeiro e o parentesco está vazio ou é equivalente a "self" (`_is_self_like`). O painel hoje deixa gravar `is_self` marcado com "mãe" escrito ao lado, o que faz a regra tratar o número como terceiro.

Novo comportamento, usado tanto no vínculo novo quanto na edição do vínculo existente: a pergunta "Este número é do próprio paciente?" vem primeiro. Se a resposta for sim, o campo de parentesco some e o painel grava `relationship = null`. Se for não, o parentesco é obrigatório e vem de uma lista fechada: mãe, pai, avó, avô, tutor(a), responsável legal, tio, tia, irmão, irmã, padrasto, madrasta, cônjuge e acompanhante. O servidor valida a mesma regra, não só a tela. Vínculos antigos com parentesco fora da lista aparecem como "acompanhante", mostrando o texto antigo ao lado para a atendente conferir, e só são regravados quando ela salvar. Para a regra da idade, acompanhante e cônjuge contam como terceiros que não são responsáveis legais.

### Vincular

O "+" ao lado da caixa de paciente abre, na folha lateral, uma busca por nome, a partir de três letras, sem diferenciar acento e maiúscula. Cada resultado mostra nome, data de nascimento e o número principal, para diferenciar homônimos. Escolhido o paciente e respondida a pergunta do "próprio paciente" (um interruptor; quando desligado aparece o parentesco numa lista suspensa), o painel cria as três linhas em `patient_contacts` (agendamento, financeiro e consulta) com a mesma marcação, de forma idempotente pela chave única `(patient_id, contact_id, role)`. A atendente não escolhe papéis. A regra da idade é aplicada na hora do envio e depende só da marcação.

Efeito conhecido e aceito: com o papel financeiro, a cobrança da taxa vai para quem agendou. Em consulta antiga sem `contact_id`, vai para todos os números financeiros, sem filtro de idade. Isso já é o comportamento atual.

### Criar ficha nova

Se a busca não achar ninguém, "Criar ficha nova" pede nome e data de nascimento, ambos obrigatórios, porque sem nascimento a regra da idade não funciona. Antes de gravar, o painel procura ficha com mesmo nome normalizado e mesma data de nascimento. Se achar, oferece vincular a ela em vez de criar outra. Depois de criar, segue o fluxo de vincular.

### Desvincular

Cada paciente na lista da caixa de paciente tem o ícone de desvincular, com confirmação, que apaga as três linhas do par. O painel recusa se aquele for o último número de um paciente com consulta futura ativa, com a mensagem "este é o único número de Fulano e ele tem consulta dia X; vincule outro número antes".

Toda ação da parte 1 grava um registro em `events` com o nome da atendente (vindo do Chatwoot).

## Parte 2: lista de consultas e nova consulta

### Lista "Consultas agendadas"

Na seção do paciente aparece a lista das consultas que ainda não aconteceram, com status `scheduled` ou `pending_reschedule`. Cada linha mostra data, hora, médico, modalidade e duração. Pagamento não aparece aqui; fica na aba Financeiro. Há duas etiquetas. "Infantil" aparece sozinha quando o paciente tem menos de 18 anos na data da consulta e não é editável. "1ª consulta" reflete `consultation_type = primeira_consulta` e pode ser ligada ou desligada pela atendente, gravando direto no banco, sem mensagem ao paciente. Se for uma primeira consulta infantil dividida com a segunda parte ainda não marcada, a lista mostra "falta marcar a 2ª parte". Cada linha tem "Editar" e "Cancelar", e embaixo fica "Nova consulta".

### Formulário de nova consulta

Os campos são médico (pré-preenchido com o da ficha), modalidade (respeitando `modality_restriction`), data, hora e duração. Nada é calculado sem a atendente ver. A duração oferece 1h e 2h, com uma sugestão pré-marcada pela regra da Eva, e 40 minutos aparece como opção extra só para a Dra. Bruna, pela regra do encaixe dela. A etiqueta "1ª consulta" vem pré-marcada conforme o histórico (sem consulta concluída antes) e pode ser trocada. Há também um campo opcional de observação da sessão, que vai para o título e a descrição do evento, como "Domiciliar".

A cobrança tem três opções: normal, taxa de reserva isenta, ou cortesia (consulta inteira sem custo). Ela vem pré-marcada conforme a ficha (`booking_fee_waived` e `custom_price = 0`), mas vale só para essa consulta.

### Primeira consulta infantil dividida

Quando o paciente é menor, a etiqueta "1ª consulta" está marcada e o médico é o Dr. Júlio, aparece a escolha entre "2h seguidas" e "dividir em dois momentos". Ao dividir, a primeira caixa fica com 1h e abre uma segunda caixa de data e hora para a outra parte, também de 1h. As duas partes recebem observações de sessão que as identificam como partes da mesma primeira consulta, e a segunda herda a situação da taxa da primeira, como `confirm_appointment` já faz. A segunda caixa pode ficar vazia se os pais ainda não souberem o horário. Nesse caso a lista mostra a pendência e a segunda parte é marcada depois por "Nova consulta", que reconhece a parte pendente e oferece completá-la.

### Encaixe

Ao salvar, o painel pede à Eva a checagem do horário contra a grade de atendimento do médico, os bloqueios (`SCHEDULE_EXCEPTIONS`) e os compromissos já existentes no Calendar. Se estiver livre, a consulta é criada direto. Se não, nada é gravado. O painel mostra o motivo ("fora do horário de atendimento", "dia bloqueado" ou "bate com a consulta de Fulano às 14h") e troca o botão por um azul "Confirmar encaixe". Só esse segundo clique cria a consulta, com a checagem pulada de propósito. O registro em `events` marca a consulta como encaixe. A mesma regra vale para a edição.

### O que a Eva faz ao criar

O endereço novo usa a mesma lógica de `confirm_appointment`: cria o evento no Calendar no formato de sempre (título, descrição, `source=psique-bot`), grava a linha em `appointments` com o número da conversa como `contact_id` (quem agendou, que é quem recebe a cobrança da taxa), apaga o evento se a gravação no banco falhar, avisa a clínica por e-mail e registra `appointment_booked` com origem "painel", nome da atendente e se foi encaixe.

### Cortesia por consulta

Hoje a cortesia só existe como `patients.custom_price = 0` e vale para todas as consultas. Entra uma coluna nova em `appointments` (`is_courtesy boolean default false`, por migração). A consulta é tratada como cortesia se a coluna for verdadeira ou se a ficha tiver preço zero, para não mudar nada dos pacientes que já são cortesia. Os pontos que passam a olhar a coluna são: `scripts/send_payment_reminders.py` (cobrança e cancelamento por falta de taxa), `app/patient_attributes.py` (`fee_status`), `dashboard/payments.py` (lista de pagamentos e planilha) e os textos de confirmação. Cada um ganha teste próprio, porque já tivemos cortesia sendo cobrada.

## Parte 3: editar e cancelar

### Editar

Permite mudar data, hora, duração, modalidade, médico, observação, cobrança e a etiqueta "1ª consulta". A alteração é feita na mesma linha de `appointments`, sem criar outra, o que mantém a taxa paga no lugar e evita consulta duplicada. O evento do Calendar é atualizado (ou recriado no calendário do outro médico, se o médico mudar). Se a data ou hora mudar, `confirmed_at` e as marcas de lembrete enviado voltam a vazio, para os lembretes valerem na data nova.

A atendente escolhe obrigatoriamente quem pediu a mudança: paciente ou clínica. Nada vem marcado. Isso grava `reschedule_initiated_by`. Pedido da clínica mantém a taxa paga sempre. Pedido do paciente segue a regra que `reschedule_appointment` já aplica: com menos de 24h da consulta original, a taxa paga não é reaproveitada. Nesse caso o painel avisa antes de confirmar: "remarcação a pedido do paciente com menos de 24h: será cobrada nova taxa". A checagem de encaixe vale aqui também.

### Cancelar

A caixa de cancelamento tem três perguntas.

A primeira é quem pediu, paciente ou clínica, obrigatória e sem nada marcado.

A segunda só aparece se a taxa foi paga e a consulta não é cortesia nem isenta: o que fazer com a taxa. "Devolver ao paciente" cancela e registra o pedido de reembolso pela mesma lógica de `register_refund_request`, ou seja, marca `refund_requested_at` e escreve a linha "Solicitação de Reembolso" na planilha de Solicitações com valor, data da consulta e motivo. A baixa depois segue o fluxo atual de `confirm_refund_completed`. "Guardar como crédito para remarcar" usa o caminho `preserve_fee` e deixa a consulta em `pending_reschedule`. "Reter a taxa" cancela sem devolver.

Embaixo aparece a dica da política, calculada pela antecedência em relação ao início da consulta. Com 24h ou mais: "pela política, a taxa não é retida". Com menos de 24h: "pela política, a taxa é retida; exceções são avaliadas pela equipe ou pelos médicos". Nesse segundo caso, se a atendente escolher devolver, o motivo fica obrigatório (por exemplo "exceção aprovada pelo Dr. Júlio").

A terceira é o motivo, opcional nos outros casos, que vai para a planilha e para `events`.

Se a consulta for uma das partes da primeira consulta infantil dividida, o painel pergunta se cancela só esta parte ou as duas. Ao confirmar, o evento sai do Calendar, o status muda e a mensagem é enviada.

## Mensagens ao paciente

Toda criação, cancelamento ou alteração de data, hora, duração, modalidade ou médico gera mensagem. Mudar só a etiqueta "1ª consulta", a observação ou a cobrança de uma consulta já existente não gera. O painel mostra o texto exato antes do clique final.

Na criação vai a confirmação padrão da Eva, na variante certa (taxa normal com PIX e prazo de 2h, taxa isenta ou cortesia). Na alteração, o texto depende de quem pediu: "a Clínica Psiquê precisou alterar sua consulta" ou "conforme combinado, sua consulta foi alterada", seguido de "era dia X às Y, agora é dia Z às W". No cancelamento, o texto acompanha a escolha da taxa: "a equipe vai providenciar a devolução da taxa", "o valor fica guardado para a próxima consulta" ou nada sobre a taxa.

Os destinatários seguem a regra da idade pela mesma função dos lembretes (`consultation_reminder_contacts`), nunca a lista crua de contatos. Quem estiver com `manual_hold` não recebe, e o painel avisa. Cada mensagem enviada é gravada em `messages` e na memória da conversa do número que recebeu, como mensagem da Eva.

Fora da janela de 24h do WhatsApp, só modelos aprovados pela Meta são entregues. Não existe hoje modelo para consulta agendada, alterada ou cancelada. Será preciso criar e aprovar três modelos. Até lá, fora da janela, o envio é tentado, e se falhar a consulta continua criada e o painel mostra "consulta registrada, mas a mensagem não foi entregue; avise o paciente por outro meio".

## Erros

Se a Eva estiver fora do ar ou responder erro, o painel mostra a mensagem e nada é alterado. Se o Calendar criar o evento e o banco falhar, o evento é apagado. Se o banco gravar e o Calendar falhar numa edição, a linha volta ao valor anterior. Se a consulta for gravada e a mensagem não sair, o painel diz isso claramente. Todas as rotas de escrita do painel continuam passando pela checagem de escopo (`_assert_*_scope`): a consulta ou o paciente precisa pertencer ao número da conversa, exceto na busca de pacientes para vincular, que por natureza procura em toda a base e por isso devolve só nome, nascimento e número principal.

## Testes

No painel (`dashboard/tests/`): vincular cria as três linhas com a mesma marcação; "próprio paciente" grava parentesco nulo; parentesco obrigatório e restrito à lista quando não é próprio; criar ficha detecta duplicada por nome e nascimento; desvincular recusa o último número com consulta futura; escopo das rotas novas; chamadas à Eva com o cabeçalho correto e tratamento de erro.

Na Eva (`tests/`): endereços novos com Calendar, Chatwoot, banco e OpenAI simulados, cobrindo criação normal, encaixe exigindo confirmação, divisão infantil com herança da taxa, cortesia e isenção por consulta, edição pela clínica e pelo paciente com e sem 24h, cancelamento com as três escolhas de taxa e gravação na planilha, cancelamento de uma ou das duas partes, destinatários pela regra da idade e `manual_hold`. As ferramentas `confirm_appointment`, `reschedule_appointment` e `cancel_appointment` mantêm os testes atuais passando depois da extração da lógica. Cortesia por consulta ganha teste em cada ponto listado na seção dela.

## Fora do escopo

Criação e aprovação dos três modelos na Meta é tarefa manual da clínica, fora do código. Privacidade de terceiros na conversa com a Eva (o "Projeto 2" dos lembretes) continua separada.

## Ordem de entrega

A parte 1 sai primeiro e sozinha, porque não depende da Eva: a moldura nova (topo com contato e lápis, caixa de paciente, abas renomeadas, fim da aba Contato) e o vínculo. A parte 2 (lista, nova consulta, encaixe, divisão infantil, cortesia por consulta) vem em seguida. A parte 3 (editar e cancelar, com reembolso) vem depois. A parte 4 aplica o visual novo às abas Financeiro, Cadastro e Resetar (com a confirmação em dois passos), sem mudar o que elas fazem. Cada parte é um PR próprio e já pode ser usada ao ficar pronta.
