# Guard: registrar receita pedida junto com mudança de dose

Data: 2026-11-16
Tamanho: mudança pequena

## Intenção e porquê

Quando o paciente pede a emissão de uma receita na mesma frase em que
questiona ou pede mudança de dose ("seria possível enviar uma receita com
Escitalopram de 20mg? a de 10mg é muito baixa pro meu estado"), a Eva segue a
regra de "problema com medicação / dúvida de dose": acolhe e orienta a escrever
direto pro e-mail da médica. Esse caminho é só texto, não tem ferramenta, e por
isso não registra nada — nem na tabela `documents`, nem evento, nem na planilha
Solicitações. O pedido de receita some.

Caso de origem: Luiz Carlos Rozendo (5581986539358), 16/11/2026. O pedido de
20mg não foi registrado e eu registrei à mão. É a mesma classe do caso
Paula/Alice (22/09/2026).

O objetivo é que o pedido de receita fique registrado mesmo embrulhado numa
dúvida de dose, sem a Eva opinar sobre a dose.

## Metas

- Quando o paciente pede emissão de documento e a resposta da Eva não chamou
  `request_document`, não chamou `nudge_doctor_document` e não transferiu para
  atendente, registrar o documento por baixo.
- Registro completo, igual ao que a `request_document` faz: `documents` + evento
  `document_requested` + planilha Solicitações + e-mail à médica.
- Para `receita`, a observação da planilha é a própria mensagem do paciente, pra
  capturar a dose pedida e acionar a detecção de medicação controlada.

## Não-metas

- Não mudar o prompt. A garantia é código, como os outros guards.
- Não registrar reclamação clínica pura (efeito colateral, "não estou bem com o
  remédio") — sem palavra de documento, o detector não dispara.
- Não mexer no caminho de handoff/transferência (já coberto pelo
  `GUARD_DOC_PROMISE`).

## Anti-metas

- A Eva nunca opina sobre dose nem diz ao paciente que a receita de 20mg será
  emitida. Decisão de dose continua 100% da médica.

## Comportamento visível (decisão da clínica: registrar em silêncio)

A mensagem que o paciente recebe não muda: a Eva continua acolhendo e mandando
ele tratar a dose por e-mail com a médica. O registro acontece por baixo, sem a
Eva dizer que registrou. A médica recebe o item na worklist e o e-mail, e decide
a dose. Para o paciente, nada de commitment.

## Abordagens consideradas

1. **Injetar `request_document` como tool call (igual ao `GUARD_DOC_PROMISE`).**
   Rejeitada: esse mecanismo zera o texto da Eva e faz a LLM reescrever a partir
   do resultado da tool, o que vira "registrei sua solicitação" — ou seja,
   confirma ao paciente. Conflita com a decisão de registrar em silêncio.

2. **Registrar por baixo e manter a resposta da Eva intacta (escolhida).**
   Fatora o miolo da `request_document` numa função async comum e chama direto
   do nó, descartando a mensagem de retorno e devolvendo a `response` sem
   alteração. A Eva fala o que já ia falar; o registro é um efeito colateral.

3. **Mudar só o prompt.** Rejeitada: prompt não é garantia (foi o que já falhou
   aqui), e a regra clínica de não mexer em dose continuaria puxando o modelo
   pro caminho sem registro.

## Como será feito

- Refactor em `app/graph/tools.py`: extrair o corpo da tool `request_document`
  (da normalização de `medication_note` até o `return`) para uma função async
  `register_document_request(...)` com os mesmos parâmetros (document_type,
  patient_email, state, config, medication_note, financial_*,
  patient_name_override). A `@tool request_document` passa a só chamar e
  retornar essa função. Sem mudança de comportamento da tool.
- Guard novo em `app/graph/nodes.py` (`patient_agent_node`), logo depois do
  `GUARD_DOC_PROMISE`. Dispara quando, no turno:
  - não é `silent_mode`;
  - a `response` (já possivelmente ajustada pelo `GUARD_DOC_PROMISE`) não tem
    `request_document`/`nudge_doctor_document` em `tool_calls`;
  - nenhum doc tool rodou antes no turno (`_doc_ran` do bloco anterior);
  - não é turno de handoff (`transfer_to_human` ausente);
  - `_detect_document_emission_request(mensagem do paciente)` devolve um tipo;
  - há `patient_email` no state.
  Então: `await register_document_request(...)` com `patient_email` do state e,
  se o tipo for `receita`, `medication_note` = mensagem do paciente. A `response`
  é devolvida sem alteração (silêncio). O dedup de 3 min já existente dentro da
  função evita linha duplicada.

## Deixado ao builder

- Nome exato do guard no log (sugestão: `GUARD_DOC_EMISSION_SILENT`).
- Posição precisa do bloco e reaproveitamento das variáveis já calculadas
  (`_patient_msg`, `_doc_ran`, `_handoff_this_turn`, `_doc_email`).

## Testes (em `tests/test_process_message.py`)

- Pedido de receita + dúvida de dose, resposta só texto sem tool → registra em
  silêncio (mock de `register_document_request` chamado uma vez, tipo `receita`,
  `medication_note` = mensagem do paciente) e a mensagem da Eva é preservada.
- Reclamação clínica pura (sem palavra de documento) → não registra.
- Modelo já chamou `request_document` → não registra de novo.
- Turno de handoff → o guard novo não dispara (quem cobre é o `GUARD_DOC_PROMISE`).
- Sem `patient_email` → não registra.
- Pure-logic do refactor: `request_document` continua delegando para
  `register_document_request` sem mudar o retorno.
