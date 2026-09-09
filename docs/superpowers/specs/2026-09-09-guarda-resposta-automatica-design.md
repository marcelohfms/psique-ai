# Guarda contra respostas automáticas de ausência

Data: 2026-09-09
Branch: `auto-reply-guard`

## Problema

Quando o celular do paciente responde a Eva com uma mensagem automática de ausência
(ex.: "Não estou disponível nesse momento. Retornarei o contato assim que possível."),
a Eva processa essa mensagem como se fosse um pedido real e chuta uma intenção.

Caso concreto (Natalia / Leonardo Pimentel, 5581996332827, 08/09/2026): o cron cancelou
a consulta por falta de pagamento às 22:50, o celular da Natalia disparou a resposta
automática de ausência dez segundos depois, e a Eva leu o "retornarei o contato" como
pedido de remarcação, respondendo "me diga qual dia e turno você prefere para remarcar".
Nenhum dado foi corrompido, mas a Eva respondeu a uma mensagem que ninguém escreveu de
verdade.

## Objetivo

A Eva deve reconhecer respostas automáticas de ausência conhecidas e não responder a elas.

## Escopo

Incluído:
- Detecção conservadora de assinaturas conhecidas de resposta automática.
- Guarda em `process_message` que sai cedo, sem rodar o grafo e sem responder.
- Registro do evento `auto_reply_ignored` para auditoria.
- Testes unitários da detecção e teste de integração da guarda.

Fora de escopo:
- Aviso à clínica quando chega resposta automática (decisão: ignorar em silêncio).
- Detecção por heurística/IA de mensagens automáticas desconhecidas. Só assinaturas
  conhecidas, para não calar paciente de verdade.

## Desenho

### Módulo novo: `app/auto_reply.py`

Função pura `is_auto_reply(text: str) -> bool`.

Normaliza o texto: minúsculas, remove acentos, colapsa espaços em branco, tira pontuação
das bordas. Depois casa contra uma lista pequena de assinaturas de alta confiança.

Regra principal (a que apareceu no caso): exige os dois sinais juntos no texto normalizado,
`"nao estou disponivel"` combinado com `"retornarei o contato"` ou `"assim que possivel"`
ou `"retorno assim que"`. Exigir a combinação evita falso positivo com um paciente que
por acaso escreva só "não estou disponível agora".

A lista de assinaturas fica isolada nesse módulo, fácil de estender quando surgir outra
mensagem automática comum.

### Guarda em `process_message` (`app/main.py`)

Logo no início de `process_message`, depois do dedup `_is_duplicate_phone_text` e antes
do trabalho do grafo:

```python
if is_auto_reply(text):
    await log_event("auto_reply_ignored", phone, {"text": text[:200]})
    logger.info("Auto-reply ignorado para %s: %.60s", phone, text)
    return
```

A mensagem já foi gravada em `messages` na ingestão (webhook Meta e Chatwoot), então o
histórico da conversa continua completo. A guarda só evita rodar o grafo e responder.

### Buffer

O buffer junta mensagens que chegam em poucos segundos e chama `process_message` com o
texto concatenado. A guarda só dispara quando o texto inteiro é uma resposta automática.
Se a mensagem automática vier colada com um pedido real do paciente, `is_auto_reply`
retorna `False` e a Eva processa normal, porque há conteúdo de verdade.

## Fluxo de dados

Webhook grava em `messages` -> `buffer_push` -> debounce -> `process_message(phone, text)`
-> `is_auto_reply(text)` verdadeiro -> `log_event` + return (sem grafo, sem resposta).

## Tratamento de erro

`is_auto_reply` é função pura e não lança. `log_event` já é fire-and-forget (engole erro).
A guarda não introduz caminho novo de falha.

## Testes

- `tests/test_auto_reply.py` (módulo novo): casos positivos (a assinatura do caso e
  variações com acento/caixa/espaços) e negativos (mensagens reais de paciente, incluindo
  "não estou disponível agora" sozinho, e pedido real colado com a resposta automática).
- `tests/test_process_message.py`: um caso garantindo que, quando `text` é a resposta
  automática, `process_message` sai cedo, não chama o grafo e registra `auto_reply_ignored`.
