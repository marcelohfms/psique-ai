# Guardrail de saída — endereço da clínica errado — Design

**Data:** 2026-07-08
**Status:** Aprovado para planejamento

## Problema

Em 08/07/2026, na conversa com a paciente Izabel (telefone 5581988054825), a Eva foi perguntada pelo endereço da clínica e respondeu com um endereço inventado ("Rua Conde de Bonfim, 344, sala 605 — Tijuca, Rio de Janeiro"), mesmo com o endereço correto (Recife-PE) presente no system prompt (`CLINIC_ADDRESS`, `app/graph/prompts.py:204`). Ao ser corrigida pela paciente, a Eva alternou entre o endereço certo e o errado várias vezes na mesma conversa, chegando a inventar que a clínica tem "duas unidades".

Já foi adicionado um reforço no prompt (`REGRA ABSOLUTA` em `CLINIC_ADDRESS`, no mesmo padrão usado em `DOCTORS_INFO`), mas isso é uma mitigação de prompt, não uma garantia — o LLM ainda pode alucinar. Este design adiciona uma segunda camada: um guardrail determinístico que intercepta a resposta antes do envio.

## Decisões de design

- **Onde interceptar:** no node do agente principal (`app/graph/nodes.py`), no mesmo trecho onde já existem os guards `GUARD_PREMATURE_CONFIRM` (linha ~1601) e `GUARD_TRANSFER` (linha ~1657) — todos operam sobre a `response` (AIMessage) retornada pela LLM, antes do envio via `send_text` (linha ~1690). O novo guard roda quando `not response.tool_calls and response.content` (resposta final em texto, sem tool call), na mesma posição dos outros dois, executado **antes** do `GUARD_TRANSFER` para que guards subsequentes já vejam o conteúdo corrigido.
- **Fonte única do endereço correto:** hoje `CLINIC_ADDRESS` (`app/graph/prompts.py:204`) mistura instrução + texto do endereço num único bloco. Extrair uma constante `CLINIC_ADDRESS_TEXT` só com as duas linhas do endereço (sem a instrução "REGRA ABSOLUTA..."), e fazer `CLINIC_ADDRESS` referenciá-la via f-string. O guard importa `CLINIC_ADDRESS_TEXT` para montar a mensagem de fallback — prompt e guard nunca podem divergir, pois compartilham a mesma constante.
- **Detecção — lista de termos proibidos:** dispara quando `response.content` (lowercase, sem acentos) contém uma palavra-gatilho de endereço (`endereco`) **E** algum termo da lista de bloqueio: `rio de janeiro`, `tijuca`, `conde de bonfim`, `duas unidades`, `duas localizacoes`. Sem chamada extra de LLM — checagem de substring, rápida e sem custo.
- **Ação ao disparar:** substitui a mensagem inteira (não tenta reescrever só o trecho do endereço). Constrói um novo `AIMessage` com o conteúdo fixo:
  ```
  O endereço da clínica é:
  {CLINIC_ADDRESS_TEXT}
  ```
  e `tool_calls=[]`, atribuído de volta à variável `response` usada pelo resto do node — assim o restante do fluxo (envio, `save_message`, gravação no checkpoint via `update["messages"] = [response]`) funciona sem alteração, sem precisar de um `return` antecipado como os outros dois guards.
- **Log:** `logger.warning("GUARD_WRONG_ADDRESS: blocked wrong address phone=%s original=%s", phone, response.content)` — só log técnico, sem alertar a clínica (mesmo padrão dos outros dois guards, que também só logam).
- **Escopo:** só cobre a resposta final em texto (sem tool call), que é onde o incidente ocorreu. Não cobre o conteúdo de tool calls (ex.: argumentos de `register_payment`), pois a Eva não usa tool calls para comunicar o endereço.

## Fluxo

```
patient_agent_node
  response = await llm.ainvoke(...)
  │
  ├─ GUARD_WRONG_ADDRESS (novo)
  │    if not response.tool_calls and response.content:
  │        if "endereco" in content_normalizado and any(termo_proibido in content_normalizado):
  │            logger.warning(...)
  │            response = AIMessage(content=f"O endereço da clínica é:\n{CLINIC_ADDRESS_TEXT}")
  │
  ├─ GUARD_PREMATURE_CONFIRM (existente, já roda com response possivelmente corrigida)
  ├─ GUARD_TRANSFER (existente)
  │
  └─ envio normal: send_text(phone, response.content) / save_message(...) / update["messages"] = [response]
```

## Testes

Seguir o padrão já usado nos outros guards em `tests/test_process_message.py` (mock de `app.graph.nodes.send_text` como `AsyncMock`, mock do LLM retornando uma `AIMessage` fixa):

- Resposta da LLM contendo "endereço" + "Rio de Janeiro" → `send_text` é chamado com o template correto (Recife), não com o texto original.
- Resposta da LLM contendo "endereço" + "Tijuca" → mesmo comportamento.
- Resposta da LLM contendo "endereço" + "duas unidades" → mesmo comportamento.
- Resposta da LLM com o endereço correto (menciona "endereço" + "Recife" ou "RioMar") → não é alterada, `send_text` recebe o texto original da LLM.
- Resposta da LLM que menciona "Rio de Janeiro" mas não é sobre endereço (ex.: "vi que você é do Rio de Janeiro, mas atendemos online") → **não** dispara, pois falta a palavra-gatilho "endereço" — cobre o caso de falso positivo discutido no design.
- `CLINIC_ADDRESS` (prompt) e `CLINIC_ADDRESS_TEXT` (guard) continuam consistentes — teste simples de que `CLINIC_ADDRESS` contém `CLINIC_ADDRESS_TEXT`.

## Fora de escopo (YAGNI)

- Alerta para a clínica (nota privada no Chatwoot ou e-mail) quando o guard disparar — só log, igual aos outros guards.
- Reescrita parcial do trecho do endereço (mantendo o resto da mensagem da LLM) — substituição total é mais simples e mais segura.
- Checagem via LLM separada para validar semanticamente o endereço — lista de termos proibidos é suficiente para o caso real observado.
- Guardrails para outros tipos de alucinação (preços, políticas) — fora do escopo deste incidente específico.

## Riscos / pontos de atenção

- **Falso positivo:** se um paciente mencionar "Rio de Janeiro" e a Eva citar isso de volta numa frase que também contenha a palavra "endereço" por coincidência, o guard dispararia incorretamente. Mitigado por exigir as duas condições (palavra-gatilho + termo proibido) e coberto por teste específico. Risco residual baixo, mas vale monitorar logs de `GUARD_WRONG_ADDRESS` nas primeiras semanas para confirmar que não há disparos indevidos.
- **Novos vazamentos de endereço errado:** se a Eva um dia alucinar um endereço diferente (não "Rio de Janeiro"/"Tijuca"/"Conde de Bonfim"), a lista de termos proibidos não pega. O reforço de prompt (`REGRA ABSOLUTA`) já feito continua sendo a primeira linha de defesa; este guard cobre especificamente a recorrência do padrão já observado.
