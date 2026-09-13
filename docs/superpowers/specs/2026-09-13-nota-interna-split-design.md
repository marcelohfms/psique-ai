# Nota interna que vaza quando não está no início da mensagem

Data: 13/09/2026
Branch: `nota-interna-split`

## Problema

Quando a Eva monta uma mensagem com uma frase para o paciente seguida de uma nota
para a equipe, a nota vaza para o paciente. A detecção em `app/graph/nodes.py`
(por volta de nodes.py:3242) usa `startswith`: só reconhece a nota interna quando
"Nota para a equipe:" está no começo da mensagem. Se vier qualquer texto antes do
marcador, a mensagem inteira, incluindo a nota, é enviada ao paciente.

Caso real: Wayne (5581999597907, 08/09/2026). A Eva enviou "Os valores das
consultas foram atualizados em junho de 2026, tá bom? 😊" seguido de "Nota para a
equipe: ... segundo a Dra. Bruna, Wayne não está mais em acompanhamento pela
clínica." O paciente leu a anotação interna.

Levantamento de 45 dias na tabela `messages`: 8 notas internas funcionaram
(marcador no início), 1 vazou (marcador no meio). É baixo volume, mas é conteúdo
clínico interno chegando ao paciente.

## Contexto que confirma a abordagem

O aviso de preço antes do marcador não é ruído. A clínica resetou de propósito os
marcadores de "aviso de preço enviado" para relembrar do reajuste de junho quem
está retomando contato. Então a frase antes do marcador é uma mensagem legítima de
paciente. Dividir preserva as duas intenções.

## O que o prompt define

Regra (b): "Nota para a equipe:" comunica algo APENAS à equipe. Regra (c): nunca
usar o marcador em mensagem destinada ao paciente. O modelo pretendido é: do
marcador em diante é conteúdo interno. O erro é a Eva grudar texto de paciente
antes do marcador.

## Solução: dividir a mensagem no marcador

Localizar a primeira ocorrência de qualquer marcador de nota interna em QUALQUER
posição do texto (case-insensitive), não só no início.

Se o marcador for encontrado, dividir:
- A parte ANTES do marcador, se tiver conteúdo após `strip()`, vai para o paciente
  pelo WhatsApp normal.
- A parte do marcador em diante vira nota privada no Chatwoot, com o mesmo fallback
  atual (se não houver `conv_id` ou a API falhar, cai para o WhatsApp, porque é
  melhor a nota chegar no lugar errado do que sumir).

Se não houver marcador, comportamento atual: tudo para o paciente.

Casos que a divisão resolve:
- Marcador no início (parte de paciente vazia): só nota privada. Igual a hoje.
- Marcador no meio (caso Wayne): paciente recebe o aviso de preço, equipe recebe a
  nota. Sem vazamento.
- Sem marcador: tudo para o paciente. Igual a hoje.

## Detalhes a preservar

O histórico em `messages` continua gravando o `response.content` completo uma vez,
igual a hoje, para a próxima leitura da LLM refletir fielmente o que ela produziu.

Quando a parte do paciente for enviada e `needs_price_notice` estiver ativo, manter
o `upsert` de `price_adjustment_notified_at`, para o aviso de preço não repetir.

## Por que é seguro

A parte interna nunca é enviada ao paciente (a não ser no fallback raro de Chatwoot
fora do ar, risco que já existe hoje e é menor, porque hoje esse formato vaza
sempre). O paciente não fica sem resposta quando havia texto legítimo antes do
marcador. Mensagem sem marcador segue idêntica ao fluxo atual.

## Fora de escopo

Por que a Eva às vezes escreve texto de paciente antes do marcador em vez de manter
a nota separada. Isso é comportamento do modelo, é raro, e a divisão já trata a
consequência sem depender de o modelo acertar o formato.

## Testes

Em `tests/test_process_message.py`, com mock de `add_private_note` e `send_text`.

Marcador no meio (caso Wayne): a parte antes vai ao paciente por `send_text`, a
parte do marcador em diante vai por `add_private_note`, e o paciente NÃO recebe o
texto da nota. Marcador no início: só `add_private_note`, nada de `send_text` ao
paciente. Sem marcador: só `send_text`, nada de `add_private_note`. Fallback:
sem `conv_id`, a nota cai para `send_text` (comportamento atual preservado).
