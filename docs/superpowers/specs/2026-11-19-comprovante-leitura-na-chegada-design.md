# Leitura do comprovante na chegada (insistir no download)

Data: 19/11/2026
Autora: Ayexa, com Claude

## Intenção e o porquê

Hoje, quando o paciente manda o comprovante como imagem sem legenda, a Eva às
vezes só responde dez a quinze minutos depois. O caso da Louise (conversa 542,
número 5581999348383) mostrou isso. O comprovante entrou no Chatwoot às 9h53 e a
Eva só confirmou por volta das 10h08, quase quinze minutos depois.

O diagnóstico de leitura mostrou a causa. O webhook do nosso app já está inscrito
no evento de chegada de mensagem do Chatwoot, então o aviso de que a imagem chegou
vem na hora. O problema é que o arquivo da imagem ainda não está pronto para ser
baixado naquele instante. O Chatwoot avisa que a mensagem chegou antes de terminar
de guardar o anexo. Nosso código tenta baixar a imagem uma única vez, não consegue,
e larga a mensagem. Só a varredura de segurança resgata depois, e ela é lenta de
propósito, porque só olha anexos com mais de dez minutos de idade.

A meta é insistir no download na chegada, por uns sessenta segundos, para ler o
comprovante em segundos na grande maioria dos casos, e deixar a varredura apenas
como última rede para o que escapar.

## Objetivos

O comprovante enviado como imagem deve ser lido e registrado em poucos segundos,
mesmo quando o arquivo demora um pouco a ficar pronto no Chatwoot.

O conserto deve reaproveitar o caminho normal da Eva e os guardas de idempotência
que já existem, para que um comprovante nunca seja registrado duas vezes nem gere
duas confirmações ao paciente.

A varredura de quinze minutos continua intacta, como rede final.

## Não-objetivos

Não vamos mexer na ingestão pelo webhook da Meta, que é um canal à parte e já tem a
sua própria correção, o PR #350, com as novas tentativas de leitura.

Não vamos encurtar a janela da varredura.

Não vamos tratar o caso raro em que o aviso de chegada vem totalmente vazio, sem nem
o link do arquivo, e o anexo só aparece num aviso posterior. Esse caso continua
coberto pela varredura. Se um dia a gente ver que ele acontece de verdade, a gente
adiciona o tratamento depois. Esta é a diferença para a versão completa que foi
considerada e deixada de lado por ser mais código para um ganho raro.

Não vamos registrar pagamento de forma automática por fora do fluxo normal. O
registro continua passando pelo `register_payment`, com todas as suas travas.

## Anti-objetivos

Seria uma falha se o conserto fizesse a Eva confirmar o mesmo comprovante duas
vezes, ou registrar o pagamento em dobro, ou agir numa conversa que está em
atendimento humano. Também seria uma falha se a espera pelo arquivo travasse a
resposta do webhook ou segurasse outras mensagens do mesmo paciente por muito tempo.

## Restrições

O Chatwoot é compartilhado. Foi verificado que a conta que o nosso app escuta tem um
único inbox, o "Psique", de id 8. O webhook e as leituras de API são todos dessa
conta, então o tráfego do outro projeto não chega até aqui. Mesmo assim, por
segurança, o resgate confere o inbox da Psique antes de agir.

O webhook do Chatwoot já responde em segundo plano. O `chatwoot_webhook` devolve o
200 na hora e processa depois, com `asyncio.create_task`. O resgate roda numa tarefa
em segundo plano à parte, então a espera pelo arquivo não trava a resposta ao
Chatwoot nem segura o fluxo da mensagem que chegou.

## Abordagens consideradas

A primeira abordagem, escolhida, é insistir no download na chegada. Quando a imagem
chega e o download falha porque o arquivo ainda não está pronto, em vez de largar, a
gente agenda uma tarefa curta em segundo plano que tenta baixar de novo algumas
vezes, com espera crescente, por uns sessenta segundos. Assim que o arquivo abre, a
imagem é lida e segue pelo caminho normal da Eva, do mesmo jeito que uma imagem que
tivesse chegado bem. É a versão simples, com pouco código, e resolve o caso da
Louise.

A segunda abordagem, a completa, era essa mesma coisa mais um gatilho extra para o
caso do aviso de chegada totalmente vazio. Foi deixada de lado por ser mais código
para um caso raro que a varredura já cobre.

A terceira, encurtar a varredura, foi descartada por deixar o atraso em minutos e
aumentar o ruído do outro projeto.

## Como vai funcionar

A peça central é uma tarefa de resgate com tempo limitado. A ideia é um módulo novo,
`app/receipt_rescue.py`, com uma função assíncrona que recebe o número do paciente,
o id da conversa, o id do anexo, o link do arquivo e a hora da chegada.

A função tenta baixar o arquivo algumas vezes, em intervalos crescentes, por exemplo
com três, oito, quinze, vinte e cinco, quarenta e sessenta segundos. Ela reaproveita
o link do arquivo que já veio no aviso de chegada, não precisa consultar a API de
novo. Enquanto o arquivo não abre, ela espera e tenta outra vez. Se depois da última
tentativa ainda não abrir, ela desiste em silêncio, e a varredura de quinze minutos
segue como rede.

Quando o arquivo abre, a função lê a imagem com a `describe_image_bytes`, a mesma do
caminho normal e da varredura. Se virou texto de comprovante ou documento, ela segue
pelo caminho normal, gravando a linha espelho em `messages` e empurrando o texto pro
`process_message`, igual a uma imagem que chegou bem.

### Onde o resgate é disparado

O gatilho é no tratador do webhook do Chatwoot, no bloco de anexos do
`_handle_chatwoot_payload`, por volta da linha 1873 do `app/main.py`. Quando a
mensagem traz um anexo de imagem ou arquivo mas o `_process_chatwoot_attachments`
devolve vazio, em vez de simplesmente cair na legenda e largar, a gente agenda a
tarefa de resgate com os dados daquele anexo. Se houver legenda, ela ainda segue
para a Eva no mesmo turno, como hoje. O gatilho só vale para o evento de chegada de
mensagem nova de entrada, o `message_created`.

### Respeito à pausa da Eva

O resgate respeita a pausa, igual a uma imagem que chega bem. Ele segue o caminho
normal e empurra pro `process_message` sem bypass. Não usa o bypass do
`replay_dropped_receipt` da varredura. O motivo é não mudar o que já acontece hoje.
Numa conversa com a Eva desligada, seja por `eva-inativa`, seja por `manual_hold`, a
imagem que chega normal já não vira registro automático, porque a atendente está
cuidando daquela conversa. O resgate mantém essa regra.

### Isolamento por inbox

O resgate só age em conversa do inbox da Psique, conferindo o `CHATWOOT_INBOX_ID`
antes de agendar ou ingerir. Como verificado, a conta só tem esse inbox hoje, mas a
checagem deixa o resgate blindado caso um dia entre outro inbox na mesma conta.

### Idempotência

Antes de ingerir, o resgate confere se aquele anexo já deixou rastro, usando os
mesmos guardas da varredura, o `has_trace` e o `has_payment_trace_until_now`, e o
`already_replayed` por id de anexo. Se já há rastro, o resgate não faz nada.

Depois de ingerir, a trava de md5 do `register_payment` é o fecho final contra
registro duplicado, a mesma que já protege hoje.

O resgate grava o `REPLAY_EVENT` com o id do anexo, além de um evento próprio de
observabilidade, por exemplo `receipt_rescued_on_arrival`, com o id do anexo, o id
da conversa e quanto tempo levou da chegada até a leitura. Assim a varredura que
rodar depois pula aquele anexo com certeza, pelo `already_replayed` e pelo
`has_trace`, e a gente consegue acompanhar quantos comprovantes foram salvos na
chegada e em quanto tempo.

### Corrida de segundos com o webhook da Meta

O resgate pode correr em paralelo com o webhook da Meta, que pode entregar a mesma
imagem logo depois. A proteção é a trava de md5 do `register_payment` mais o
`has_trace`. Como os dois caminhos leem a mesma imagem, eles geram o mesmo md5, e o
segundo a gravar é barrado. Fica um resíduo mínimo e conhecido, o caso de uma baixa
manual pelo painel sem md5 acontecer nos mesmos poucos segundos. Esse risco já existe
hoje na varredura, numa janela bem maior, e é aceito. Não vamos criar trava nova
entre os dois caminhos por causa disso.

## Testes

Seguindo o TDD e o padrão do projeto, os testes vêm antes da implementação e com as
dependências externas mockadas, já que o resgate toca Chatwoot, OpenAI e Supabase.

A lógica do resgate fica no módulo novo `app/receipt_rescue.py`, então ela ganha um
arquivo de teste próprio, `tests/test_receipt_rescue.py`. O disparo do resgate, que
é a fiação dentro do `_handle_chatwoot_payload`, é testado no arquivo que já cuida do
webhook, o `tests/test_webhook.py`.

Os casos a cobrir são estes. Quando o processamento do anexo na chegada devolve vazio
para uma imagem, o resgate é agendado com a conversa, o número e o anexo certos, e só
no evento de mensagem nova. O resgate, quando o download falha na primeira tentativa
e funciona na segunda, ingere uma única vez, gravando a mensagem e chamando o
`process_message`, e registra o evento de resgate. O resgate não faz nada quando já
existe rastro daquele anexo. O resgate não age em conversa fora do inbox da Psique.
O resgate desiste sem erro e sem ingerir quando estoura o número de tentativas.

Todos os testes precisam passar no CI antes do merge.

## O que fica a critério de quem implementa

Os valores exatos dos intervalos entre tentativas e o número de tentativas podem ser
ajustados, começando pelos sugeridos acima, e ficam como constantes do módulo, no
mesmo estilo das constantes do `app/media.py`. O nome e o conteúdo exato do evento de
observabilidade também ficam abertos, desde que carreguem o id do anexo para a
idempotência e o acompanhamento.
