# Explicação antecipada da 1ª consulta de menor de idade com Dr. Júlio

**Data:** 2026-07-31
**Status:** Aprovado (aguardando plano de implementação)

## Problema

A regra que explica ao responsável que a primeira consulta de um paciente
menor de 18 anos com o Dr. Júlio acontece em duas partes de 1h só é aplicada
hoje via `MINOR_RULE` (app/graph/prompts.py:196-219), dentro do
`patient_agent_node`, e só quando a própria Eva (LLM) decide chamar
`get_available_slots` para agendar. Isso significa que a explicação depende de:

1. o LLM lembrar de explicar antes de buscar horários (não há nada que force
   isso no código); e
2. o fluxo de agendamento nunca desviar para um atendente humano antes disso.

Caso real (Bernardo Lima Beltrão Teixeira, 5581987415206, 31/07/2026): o
cadastro identificou corretamente menor de idade + primeira consulta + Dr.
Júlio, mas o paciente mandou uma mensagem ambígua ("Data"), o modelo pulou
direto para `get_available_slots` sem perguntar/explicar, não havia bloco de
2h disponível, a tool devolveu "não encontrei horários... use
transfer_to_human", e a Eva transferiu para a atendente sem nunca ter dado a
explicação. A atendente resolveu manualmente com data/hora exatas, o que cai
em `ATTENDANT_INSTRUCTION_RULE` caso (d) primeiro item
(app/graph/prompts.py:10-20) — que confirma o agendamento direto e
explicitamente proíbe qualquer resumo/pré-confirmação. A explicação nunca
chega ao responsável.

## Decisão

Mover a explicação para ser disparada de forma determinística (sem depender
de decisão do LLM) assim que as 3 condições forem identificadas durante o
**cadastro** (`collect_info_node`), em vez de esperar o momento do
agendamento. Isso garante que o responsável seja informado independentemente
do que acontece depois (agendamento direto pela Eva, ou desvio para
atendente).

A pergunta prática de logística ("prefere as duas seguidas ou em dias
separados?") continua sendo feita só na hora de agendar (`MINOR_RULE`), pois
depende de disponibilidade real de horário — não faz sentido perguntar isso
durante o cadastro.

## Condição de disparo

```
idade_paciente < 18
AND is_returning_patient is False
AND preferred_doctor == "julio"
AND not state.get("minor_first_consult_explained")
```

Avaliada a cada turno dentro de `collect_info_node`, usando o estado já
mesclado com o que for extraído/persistido naquele turno (`_persistent_updates`
+ campo(s) recém-extraídos) — não apenas o `state` anterior ao turno. Isso é
necessário porque as 3 condições podem ficar completas em qualquer ordem:

- Fluxo padrão: idade e "primeira vez" já conhecidas, médico só é perguntado
  no fim do cadastro (step 9) → condição fica completa quando o médico é
  extraído.
- Caso Bernardo: médico já mencionado na primeira mensagem (auto-detectado no
  topo de `collect_info_node`, app/graph/nodes.py:387-410, antes de qualquer
  step) → condição fica completa quando `is_returning_patient` é extraído
  (step 4), pois idade e médico já estão no estado.

## Mudança

**Novo campo em `ConversationState`:** `minor_first_consult_explained: bool`
(default `False`/ausente). Marcado `True` na primeira vez que a explicação é
enviada; nunca mais dispara depois disso para a mesma conversa.

**`app/graph/nodes.py` — `collect_info_node`:**
- Novo texto de explicação (extraído de `MINOR_RULE` — ver abaixo), formatado
  com `{patient_name}`, enviado como **uma única bolha de WhatsApp**, como
  prefixo da próxima pergunta pendente do cadastro (mesmo padrão já usado
  hoje para a mensagem "Sem problema, seguirei com o Dr. Júlio...").
- A checagem roda dentro dos dois pontos de saída existentes de
  `collect_info_node` — `_ask(reply)` e `_extract_and_ask(extracted,
  next_q)` — que já concentram toda saída de pergunta do state machine.
  Cálculo da condição usa `state` mesclado com `_persistent_updates` e, no
  caso de `_extract_and_ask`, também com `extracted`.
- Quando a condição é satisfeita nesse turno: prefixa a explicação à
  mensagem que já seria enviada (a próxima pergunta do cadastro) e inclui
  `minor_first_consult_explained: True` no dict retornado.

**`app/graph/prompts.py` — `MINOR_RULE`:**
- Passa a existir em duas variantes, escolhidas em `patient_agent_node`
  conforme `state.get("minor_first_consult_explained")`:
  - **Já explicado (comportamento novo, caminho comum):** variante reduzida —
    só instrui a perguntar a preferência (2h seguidas vs. sessões separadas)
    e a lógica de `slot_duration_minutes` / `session_note`. Não repete a
    explicação de duas partes de 1h.
  - **Não explicado ainda (rede de segurança):** mantém o texto completo
    atual — explica E pergunta a preferência antes de buscar horários. Cobre
    qualquer caso de borda em que o novo mecanismo não tenha dado tempo de
    disparar (ex.: conversas já em andamento no momento do deploy, ou algum
    caminho de estado não previsto).
- `MINOR_RETURNING_RULE` e `ADULT_RULE` não mudam.

**Seleção da variante em `patient_agent_node`** (app/graph/nodes.py, região
~1514-1540, onde `duration_rule` já é escolhido entre `MINOR_RULE` /
`MINOR_RETURNING_RULE` / `ADULT_RULE`): adicionar a checagem de
`minor_first_consult_explained` para escolher entre as duas variantes de
`MINOR_RULE`.

## Fora de escopo

- Perguntar a preferência de logística (2h seguidas vs. separado) durante o
  cadastro — continua sendo perguntado só na hora de agendar, quando há
  disponibilidade real para oferecer.
- Qualquer mudança em `ATTENDANT_INSTRUCTION_RULE` ou no comportamento de
  transferência para atendente — a explicação antecipada resolve o problema
  sem precisar mexer nesse caminho.
- Persistir `minor_first_consult_explained` no banco (Supabase) — vive só no
  checkpoint do LangGraph; se o estado for perdido, o pior caso é a
  explicação sair de novo (redundante, não incorreto), coberto pela própria
  rede de segurança do `MINOR_RULE` completo.
- Mudar o texto/conteúdo da explicação em si — só muda quando ela é enviada.

## Testes

`tests/test_process_message.py` (ou arquivo equivalente que já cobre
`collect_info_node`):
- Fluxo padrão: menor + primeira consulta, médico perguntado só no fim →
  explicação sai assim que o médico é confirmado como "julio", prefixada à
  pergunta de e-mail; `minor_first_consult_explained` vira `True` no estado
  retornado.
- Caso "Bernardo": médico mencionado já na primeira mensagem (auto-detectado
  antes dos steps) → explicação sai assim que `is_returning_patient=False` é
  extraído (step 4), prefixada à pergunta de CPF do paciente.
- Paciente adulto com Dr. Júlio → nunca recebe essa explicação;
  `minor_first_consult_explained` nunca é setado.
- Menor de idade com Dra. Bruna → nunca recebe essa explicação.
- Menor de idade em consulta de acompanhamento (`is_returning_patient=True`)
  → nunca recebe essa explicação.
- Explicação já enviada em turno anterior (`minor_first_consult_explained=True`
  no estado de entrada) → não é enviada de novo em turnos seguintes.

`tests/test_tools.py` ou onde `patient_agent_node`/`MINOR_RULE` já é testado:
- `minor_first_consult_explained=True` no estado → prompt usa a variante
  reduzida (só pergunta preferência, sem reexplicar).
- `minor_first_consult_explained` ausente/`False` no estado ao chegar em
  `patient_agent_node` (rede de segurança) → prompt usa o texto completo
  atual (explica + pergunta).
