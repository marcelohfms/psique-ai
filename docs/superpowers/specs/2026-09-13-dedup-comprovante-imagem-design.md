# Dedup de comprovante de imagem processado em dobro

Data: 13/09/2026
Branch: `dedup-comprovante-imagem`

## Problema

O mesmo comprovante de pagamento às vezes é processado duas vezes seguidas, com
segundos de diferença. Cada processamento chama `register_payment`, então o mesmo
pagamento é gravado duas vezes e o paciente recebe duas mensagens de sucesso.

Levantamento dos últimos 90 dias na tabela `events` (evento
`payment_receipt_registered`, cruzado por `drive_link`): 374 registros no total,
cinco duplicatas reais. Os intervalos foram de 11, 20, 52, 122 e 699 segundos. O
caso mais recente é o Renato Lima Lopes de Freitas (telefone 34637036406,
05/09/2026), com dois "Pagamento Parcial" de R$100 a 52 segundos um do outro.

A causa é a corrida de processamento duplo do turno, já conhecida e registrada na
memória do projeto. Nenhum dedup existente pega essa corrida. Como é o mesmo
arquivo chegando duas vezes, o `drive_link` é idêntico nas duas passagens. Foi
assim que as cinco duplicatas foram identificadas.

## O que NÃO é este problema

Reenvio do mesmo comprovante em outro dia não é este bug. Não é uma rajada, é um
envio novo e separado, que merece uma resposta própria. Se a consulta já está
quitada, o guard atual de `paid_at` (tools.py:3723) já responde "já estava
registrado" com uma mensagem só. O único resíduo, taxa de R$100 reenviada dias
depois antes da quitação, é indistinguível pelo conteúdo de um pagamento parcial
legítimo de R$100. Não ocorreu nenhuma vez em 90 dias. Fica de fora deste
trabalho, por decisão consciente, para não arriscar barrar um parcial verdadeiro.

## Chaves de dedup descartadas

ID da transação PIX (E2E): só 21% dos comprovantes de imagem em 90 dias tinham um
ID extraível. O resto vem mascarado pela leitura da imagem (por exemplo
"E00000000020260..."). Pior, os mascarados colidem entre si, então diferentes
pagamentos do mesmo banco compartilham o mesmo prefixo. Descartado por baixa
cobertura e risco de falso positivo.

Filtro genérico de mensagem repetida no envio: em 90 dias houve 85 mensagens
idênticas consecutivas, e a maioria é legítima. São as repetições do cadastro,
como "É a primeira consulta ou o paciente já está em acompanhamento na clínica?".
Um filtro de texto repetido travaria essas conversas. Descartado.

## Solução

Duas camadas, cada uma com uma responsabilidade única.

### Camada 1: dedup no dado (register_payment)

Antes de qualquer escrita em `register_payment`, consultar a tabela `events`
procurando um `payment_receipt_registered` com o mesmo `drive_link`, para as
variantes do mesmo telefone (`_phone_variants`), dentro de uma janela de 30
minutos. Se achar, a tool não grava nada. Não grava linha na planilha, não grava
evento, não atualiza `appointments`.

Este é o mesmo padrão do guard de `request_document` (PR #182, tools.py:2690), que
já roda em produção. A janela de 30 minutos reusa a constante
`_BOOKING_FEE_RESEND_WINDOW_MINUTES` que o PR #219 já criou. Pelos dados, essa
janela pega as cinco duplicatas reais, sendo a mais lenta de 11,6 minutos, e fica
a 26 horas de distância do reaproveitamento legítimo mais próximo.

Escopo: só comprovante de imagem. O guard só roda quando há `drive_link` e não é
lançamento do painel da atendente. Lançamentos com `is_link=True` ou
`payment_method` preenchido passam direto, porque não têm `drive_link` e não são
o alvo deste bug.

Quando o guard dispara, a tool devolve um resultado marcado com um sentinela
próprio (por exemplo o marcador `RECEIPT_DEDUP_MARKER`), sinalizando que aquele
arquivo já tinha sido registrado. Nada é gravado.

### Camada 2: supressão da mensagem (patient_agent_node)

O nó detecta o marcador de dedup no resultado da `register_payment` e encerra o
turno sem enviar nada ao paciente. Assim o paciente recebe uma única mensagem de
sucesso, a do primeiro processamento.

Isto espelha o `GUARD_SLOT_TAKEN_VERBATIM` (nodes.py:2030), que já intercepta o
resultado de `register_payment` no bloco de ToolMessages finais e encerra o turno
com um `return`, sem deixar a LLM re-sintetizar. A diferença é que aqui o turno
termina em silêncio, sem `send_text` e sem `save_message` de uma mensagem nova.

O ponto que torna isto seguro é que a supressão não compara texto. Ela só acontece
quando a própria tool afirmou que já tinha registrado aquele arquivo. Nenhuma
repetição de pergunta do cadastro passa por este caminho.

## Por que é seguro para o fluxo atual

O problema das duas mensagens é, por natureza, uma corrida com o mesmo arquivo em
segundos. A janela curta é do tamanho exato desse problema. Envio em outro dia não
é rajada, gera uma resposta por dia, que é o certo. O guard só toca no caminho de
comprovante de imagem. Lançamento do painel, reenvio legítimo horas depois e
pagamento parcial verdadeiro seguem intactos.

## Testes

Unitários e de integração com mock de Supabase, no estilo já usado no projeto.

Primeira camada, em `tests/test_tools.py`. Segundo comprovante com o mesmo
`drive_link` dentro de 30 minutos não grava planilha, evento nem appointment, e
devolve o marcador de dedup. Segundo comprovante com `drive_link` diferente grava
normalmente. Reenvio com o mesmo `drive_link` fora da janela (mais de 30 minutos)
não é tratado como duplicata. Lançamento do painel (`is_link` ou `payment_method`)
nunca entra no guard.

Segunda camada, em `tests/test_process_message.py`. Resultado de
`register_payment` com o marcador de dedup encerra o turno sem `send_text` ao
paciente. Resultado normal segue o fluxo atual e envia a mensagem de sucesso.

## Fora de escopo

Proteção para taxa reenviada em outro dia antes da quitação. Dedup dos lançamentos
do painel da atendente, que não têm `drive_link`. Dedup do "Recebemos seu
documento", que teve o mesmo padrão de repetição e pode reusar esta abordagem
depois, em trabalho separado.
