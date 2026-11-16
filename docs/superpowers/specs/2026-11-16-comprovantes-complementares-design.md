# Comprovantes complementares e valor rígido da consulta

Data: 2026-11-16

## Intenção e o porquê

Hoje a Eva calcula o saldo de uma consulta descontando só a taxa de reserva. A conta é o valor da consulta menos cem, e nada mais. Ela não soma os PIX parciais que já entraram. Quando o paciente completa o valor total em mais de um PIX, cada parcela é comparada com o saldo inteiro de novo, porque a Eva esqueceu do que já foi pago antes.

O efeito prático é este. Uma consulta de seiscentos e cinquenta, taxa de cem já paga, saldo de quinhentos e cinquenta. O paciente manda trezentos, a Eva registra como Pagamento Parcial e diz que falta duzentos e cinquenta, tudo certo. Depois ele manda os duzentos e cinquenta que faltavam. Nesse segundo PIX a Eva volta a comparar com os quinhentos e cinquenta cheios, registra outro Pagamento Parcial, diz que ainda falta trezentos, e a consulta nunca é marcada como quitada. O campo `paid_at` fica vazio para sempre.

Queremos que a Eva receba PIX complementares com segurança. Que ela guarde um total do que já foi pago naquela consulta, some a taxa já paga mais todas as parcelas, e feche a consulta como quitada no momento em que esse total alcança o valor da consulta. Junto disso, queremos deixar rígida a regra do valor total. A consulta vale o que a tabela de preço diz, e ninguém fecha por menos, salvo o paciente que tem valor especial cadastrado na ficha.

## Objetivos

Guardar, por consulta, quanto já foi pago no total, somando a taxa de reserva e todas as parcelas, tanto as registradas pela Eva quanto as registradas pelo painel.

Decidir a quitação por esse total acumulado. A consulta vira quitada, com `paid_at` preenchido, quando o acumulado alcança o valor da consulta.

Mostrar ao paciente, a cada parcela, o saldo real que ainda falta, descontando tudo que já entrou.

Deixar o valor total da consulta rígido. A quitação só acontece quando o acumulado alcança o valor da tabela de preço, calculado pelo médico, idade, tipo de consulta e data. A única exceção é o paciente com `custom_price` cadastrado, que fecha pelo valor especial dele. A Eva nunca fecha por um valor que a conversa sugira.

Manter esse total correto quando a consulta é remarcada, do mesmo jeito que os outros campos de pagamento já são tratados na remarcação.

## Não objetivos

Não vamos tratar duas imagens de comprovante que chegam na mesma mensagem. Os PIX complementares chegam separados, em mensagens ou dias diferentes, e é só isso que este trabalho cobre.

Não vamos mudar a leitura do valor do comprovante pela visão. O que a Eva lê de cada imagem continua igual.

Não vamos mexer na dedup por md5 nem na trava de reenvio da taxa, além do ajuste mínimo que o acúmulo exigir.

## Anti objetivos

Seria uma falha se a Eva passasse a fechar uma consulta por menos do que a tabela manda, confiando num valor dito pelo paciente.

Seria uma falha se o acúmulo contasse o mesmo comprovante duas vezes e inflasse o total.

Seria uma falha se uma parcela deixasse de aparecer no total e o paciente fosse cobrado de novo por algo que já pagou.

## Restrições

O valor da consulta já é calculado de forma determinística em `_expected_consultation_amount`, pela tabela por médico, idade, tipo e data, com o desconto de cinquenta no PIX embutido, e com a exceção do `custom_price`. Essa função continua sendo a única fonte do valor total.

A fonte da verdade do pagamento é a tabela `appointments`, mais a planilha Pagamentos e o Drive. A tabela `payments` está vazia e sem uso. A planilha não tem dedup e não é confiável para somar.

O painel registra pagamento por um caminho próprio em `dashboard/payments.py`, que hoje preenche `booking_fee_paid_at` ou `paid_at`. Qualquer solução precisa valer para a Eva e para o painel.

A remarcação em `reschedule_appointment` preserva `booking_fee_paid_at` e zera `confirmed_at`. O novo campo de total precisa seguir a mesma regra que a taxa já segue, inclusive quando a política recolhe a taxa antiga na segunda remarcação.

Este histórico de pagamentos já teve muitos bugs de amarração frágil por texto. A solução precisa ser um número confiável no banco, não uma soma reconstruída de eventos em texto.

## Caminhos considerados

O primeiro caminho era somar na hora. A cada comprovante, a Eva leria os eventos de pagamento anteriores daquela consulta, somaria os valores já registrados e descontaria do total. Não mexe no banco e não precisa de preenchimento inicial. Foi descartado pela fragilidade. Os valores ficam gravados como texto, o casamento do evento com a consulta é pela data em texto, que muda na remarcação, e o painel grava com outro nome de evento. É exatamente o tipo de amarração que já deu dor de cabeça aqui.

O segundo caminho é uma coluna acumuladora no `appointments`. Um número em centavos que soma a cada registro, pela Eva e pelo painel. O saldo vira o valor da consulta menos essa coluna, e a consulta é marcada como quitada quando a coluna alcança o total. É um número único e confiável, que sobrevive à remarcação junto com os outros campos de pagamento. Em troca pede uma migração, um preenchimento dos pagamentos já existentes, e mexer no painel e na lógica de remarcação.

Escolhido o segundo caminho, a coluna acumuladora, pela confiabilidade. A Ayexa confirmou a escolha.

## Como fica

### A coluna nova

Uma coluna nova em `appointments`, chamada `amount_paid_cents`, inteiro, padrão zero. Ela guarda o total de dinheiro recebido para aquela consulta, em centavos, incluindo a taxa de reserva e todas as parcelas.

A migração entra em `supabase/migrations`, no padrão `ADD COLUMN IF NOT EXISTS`. Depois da migração roda um preenchimento único dos registros antigos, descrito mais abaixo.

### O registro pela Eva

Em `register_payment`, cada pagamento real que for gravado soma o valor pago em `amount_paid_cents` da consulta, por cima do que já estava lá. Isso vale para a taxa de reserva, para a parcela e para o pagamento que quita. Os caminhos que não gravam dinheiro novo não somam, ou seja, a dedup de mesmo md5, o reenvio da taxa dentro da janela, a dedup de mesmo drive_link e o valor ilegível continuam sem tocar na coluna.

O saldo passa a ser calculado a partir da coluna. O valor que ainda falta é o valor da consulta menos `amount_paid_cents`, e nunca mais o valor da consulta menos cem fixo. A decisão de tipo muda de base. Quando o acumulado depois desta parcela alcança o valor da consulta, o tipo é Consulta e a Eva marca `paid_at`. Quando ainda falta, o tipo é Pagamento Parcial e a Eva mostra o saldo real que falta. A taxa de reserva continua sendo reconhecida e rotulada como Taxa de Reserva no primeiro pagamento de cem, como já é hoje.

A mensagem do saldo ao paciente passa a usar o saldo real calculado pela coluna, em todos os ramos onde hoje aparece saldo restante.

### O registro pelo painel

Em `dashboard/payments.py`, o caminho que hoje preenche `booking_fee_paid_at` ou `paid_at` também soma em `amount_paid_cents` o valor que a atendente de fato recebeu naquela baixa, não o preço cheio da consulta. Isso corrige um detalhe que o revisor apontou. Hoje a pendência de consulta no painel nasce com o preço cheio da tabela, calculado em `_calc_valor_consulta`, e esse valor chega cru em `mark_paid`. Se a Eva já somou cem da taxa e a atendente desse baixa de consulta com o preço cheio, o acumulado estouraria o total.

A decisão técnica para resolver isso é esta. O painel passa a mostrar, para uma consulta que já tem pagamento parcial, o saldo que ainda falta, não o preço cheio. A baixa soma em `amount_paid_cents` exatamente o valor exibido e recebido. Assim a taxa mais a baixa do painel fecham no valor da consulta, sem estourar.

Os pagamentos confirmados pela atendente seguem com a mesma autoridade de hoje. Cartão, dinheiro, link e cortesia continuam quitando direto, sem passar pela trava do valor rígido, porque quem confirma é a clínica. A trava do valor rígido governa o caminho automático do comprovante PIX e a baixa parcial. Essa é uma decisão minha, registrada aqui para você conferir.

### O valor rígido

A quitação fica amarrada ao valor de `_expected_consultation_amount`. A Eva só marca `paid_at` quando `amount_paid_cents` alcança esse valor, calculado pela tabela por médico, idade, tipo e data. O único desvio permitido é o `custom_price` da ficha do paciente, que a função já respeita. A cortesia, que é `custom_price` igual a zero, continua quitando direto como já faz. A Eva não fecha a consulta por um valor dito na conversa, nem aceita um total menor que o da tabela.

### A remarcação

Aqui o desenho inicial descrevia um comportamento que o código não tem, e o revisor corrigiu. Em `reschedule_appointment` a mesma linha é atualizada no lugar, e os campos de pagamento são preservados simplesmente por não serem tocados. Então `amount_paid_cents` fica preservado sozinho, sem código novo, do mesmo jeito que `booking_fee_paid_at` já fica. Não existe um ponto nessa função para zerar o total, e não precisa existir.

A recolha da taxa na segunda remarcação também não acontece dentro dessa função. Os backstops da segunda remarcação e das vinte e quatro horas fazem um retorno antecipado e não remarcam nada. A recolha ocorre depois, quando a Eva chama cancelamento mais um novo agendamento, e isso cria uma linha nova com outro identificador, que já nasce com o total em zero. Ou seja, o caso da taxa recolhida se resolve sozinho pela linha nova, sem precisar zerar nada na linha antiga.

### O saldo que o paciente lê

Este ponto o desenho inicial subestimava, e o revisor destacou. O saldo que o paciente vê não sai só do retorno de `register_payment`. Ele também é montado no bloco de consultas realizadas com saldo pendente em `app/graph/nodes.py`, e por tabelas de saldo fixas no prompt em `app/graph/prompts.py`. Essas tabelas hoje assumem que só a taxa foi paga e calculam saldo como preço menos cem. Depois de uma parcela complementar, elas informariam saldo errado ao paciente, não importa o que a tool devolva.

Para cumprir a promessa de mostrar o saldo real a cada parcela, o saldo que entra no prompt e no bloco de `nodes.py` passa a ser o valor da consulta menos `amount_paid_cents`. Quem constrói precisa levar o acumulado, ou o restante já calculado, até esses dois lugares, e não deixar a conta antiga sobreviver em nenhum deles.

### A primeira consulta dividida em duas sessões

Quando a primeira consulta infantil é dividida em duas sessões de uma hora, há duas linhas ligadas. Hoje a função interna que atualiza os campos grava o mesmo valor em todas as linhas ligadas, e o saldo é lido da primeira. A coluna acumuladora segue a mesma convenção. O total do pacote inteiro é gravado igual em todas as linhas ligadas, e a leitura continua pela primeira. Assim o pacote dividido não soma dobrado nem some uma parte. Decisão minha, registrada para conferência.

### Detalhes técnicos que ficam decididos

A soma precisa ser atômica. O padrão ingênuo de ler, somar e gravar não é atômico no Supabase, e duas baixas ao mesmo tempo, uma pela Eva e outra pelo painel, perderiam uma parcela, o que fere o anti objetivo. A soma vai por uma função no banco que incrementa de forma atômica, ou por um update condicional equivalente.

A unidade é centavos. O valor chega como texto e é convertido para centavos com arredondamento na hora de somar.

O campo `booking_fee_paid_at` continua sendo gravado no pagamento parcial como hoje, porque o cron de cobrança lê esse campo. A coluna nova não substitui esse marcador, ela convive com ele.

Taxa isenta e cortesia não entram no acúmulo. Isenta é dinheiro que não foi recebido, e cortesia tem preço zero e já quita direto.

### O preenchimento dos registros antigos

Um script único, no padrão dos outros em `scripts`, preenche `amount_paid_cents` das consultas que já têm pagamento. Para quem já tem `paid_at`, a coluna recebe o valor da consulta, porque está quitada. Para quem tem só `booking_fee_paid_at`, a coluna recebe cem, ou o que os registros de parcela daquela consulta somarem, quando der para reconstruir com segurança. O script roda em teste primeiro, mostrando o que faria, e só então grava. Fica idempotente, para poder rodar de novo sem dobrar nada.

### O pagamento a mais

Decidido pela Ayexa. Quando o acumulado passa do valor da consulta, a Eva marca a consulta como quitada e avisa a clínica do excedente, sem mover dinheiro sozinha. Não há crédito automático para a próxima consulta. A mensagem ao paciente não mostra saldo negativo. O saldo nunca aparece abaixo de zero, e o excedente fica a cargo da clínica resolver.

### A forma de pagamento

Decidido pela Ayexa. Por agora a soma automática cobre só os comprovantes PIX. O valor da consulta já tem o desconto de cinquenta embutido, válido no PIX, e é contra esse valor que a soma fecha. Cartão e dinheiro continuam como a baixa cheia da atendente no painel, fora da soma automática. O pagamento misto, parte PIX e parte cartão, não é tratado neste trabalho.

## O que fica a cargo de quem construir

Decidir exatamente como somar as parcelas já existentes no preenchimento inicial, lendo os eventos de pagamento ou a planilha, e o que fazer quando os dois discordam. O desenho pede que a quitação já marcada seja respeitada e que a reconstrução seja conservadora.

Ajustar os testes que cobrem `register_payment`, seguindo a estrutura de um arquivo por camada. Em especial, os testes de pagamento parcial, de saldo de consulta antiga, de reenvio da taxa e de cortesia precisam continuar passando, e entram testes novos para a soma de parcelas complementares ao longo do tempo até a quitação, e para a trava do valor rígido.

Conferir se há algum outro ponto do código, além do retorno da tool, do bloco de `nodes.py` e das tabelas do prompt já citados, que leia o saldo da conta antiga, o valor menos cem fixo, e passar a usar a coluna.

Implementar a soma atômica no banco e cobrir com teste a corrida entre uma baixa do painel e um comprovante da Eva no mesmo instante.
