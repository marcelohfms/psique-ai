## O que aconteceu

Conversa de 04/08/2026, telefone 5587996089614. Mãe agendando para a filha Beatriz, 20 anos. A Eva respondeu:

> Como é a primeira consulta infantil, ela será exclusivamente online, com duração de 2 horas, ok?

As três afirmações são falsas. O slot (quinta 18h do Dr. Júlio) é `escolha`, ou seja online **ou** presencial; a paciente é adulta; a consulta é de 1 hora. Em seguida a Eva cobrou R$ 850,00, valor de primeira consulta infantil, quando o correto era R$ 700,00.

O agendamento foi gravado como `modality='online'` sem que ninguém tivesse perguntado.

São dois bugs independentes que se somaram na mesma frase.

## Bug 1 — a modalidade sumia da saída de `get_available_slots`

Todos os formatos de horário anexam `[apenas online]` / `[online ou presencial — paciente escolhe livremente]`, **menos** os que agrupam por turno. Esse é justamente o caminho mais comum, já que a Eva pergunta o dia antes do turno.

O checkpoint do thread mostra o que a tool devolveu de verdade:

```
Horários disponíveis para quinta-feira, dia 06/08:
- Manhã: 09:00, 10:00
- Noite: 18:00
```

Sem etiqueta, a regra de MODALIDADE DE ATENDIMENTO do prompt não tem em que se apoiar, e a Eva preenche a lacuna sozinha.

**Correção:** extraído `_times_with_modality()` e aplicado nos 3 pontos que descartavam a informação.

> Existe um 4º ponto, em `_search_month_shift`, que veio no commit 05b9151 e ainda não está na `main`. Ele já está corrigido no branch `fix/reschedule-guard-resume` e entra junto quando aquele branch for mergeado. Espere conflito em `tools.py`: a resolução é ficar com as duas versões, o helper é idêntico.

## Bug 2 — `ADULT_RULE` não impedia tratar adulto como consulta infantil

Aqui o código acertou e a LLM ignorou. Percorrendo o histórico de checkpoints:

| passo | estado |
|---|---|
| 13 | `patient_age=20` — correto desde o início |
| 31 | Eva pergunta o parentesco, pergunta reservada a `is_patient=false E < 18 anos` |
| 35 | Eva chama `get_available_slots(slot_duration_minutes=120)` |
| 40 | "primeira consulta infantil … 2 horas" |

Como `is_minor` (20 < 18) é falso, `MINOR_RULE` e `GUARDIAN_RULE` nunca entraram no prompt e o `ADULT_RULE` foi injetado corretamente. O problema é que o `ADULT_RULE` era uma linha só: *"Use slot_duration_minutes=60"*. Nada proibia a palavra "infantil", os 120 minutos ou o preço infantil. A Eva concluiu a partir de "Para minha filha" + "é a primeira consulta" + a própria pergunta de parentesco.

**Correção:** regra ampliada com as três proibições explícitas, e com a afirmação de que só a idade define menoridade — ser filho(a) de quem está no WhatsApp, não.

Adicionado também, nos dois prompts de agendamento, um guard proibindo afirmar modalidade sem etiqueta ou restrição cadastral. Defesa em profundidade, caso algum formato futuro volte a omitir a informação.

## Refactor de apoio

A seleção de regra por idade saiu de dentro de `patient_agent_node` para `_duration_rule_for()`. Já era lógica pura; agora é testável sem montar o nó inteiro.

## Verificação

`600 passed`, incluindo 6 testes novos:

- 2 em `test_tools.py` — todo horário listado vem etiquetado, no caminho normal e no fallback de 1h. Escritos falhando primeiro, contra a saída real do bug.
- 4 em `test_process_message.py` — conteúdo do `ADULT_RULE` e seleção de regra para adulto com responsável, menor em primeira consulta e menor em acompanhamento.

Além disso, `scripts/_verify_modality_labels.py` refaz a chamada da conversa original contra o Google Calendar **real** (somente leitura, não agenda nada) e conta quantos horários saíram etiquetados:

```
=== julio | quinta-feira | turno qualquer | 60 min ===
- Manhã: 09:00 [online ou presencial — paciente escolhe livremente], ...
- Noite: 19:00 [online ou presencial — paciente escolhe livremente]
OK   5/5 horários com etiqueta de modalidade

=== bruna | sexta | ... ===
  - Manhã: 08:00 [online ou presencial — paciente escolhe livremente], ...
  - Tarde: 13:00 [apenas online], 14:00 [apenas online], 15:00 [apenas online]
OK   13/13 horários com etiqueta de modalidade
```

O dia misto da Bruna (manhã `escolha`, tarde `apenas online`) bate exatamente com `DOCTOR_SCHEDULES`.

## Limite do que está provado

O Bug 1 era de código e está provado nas duas camadas acima: a informação agora chega até a Eva.

Os dois guards de prompt são instrução para a LLM. Os testes garantem que o texto está no prompt, não que o modelo vai obedecer. A prova de verdade é uma conversa nova — sugiro simular o mesmo roteiro (consulta para filha, nascimento em 2006, primeira consulta, Dr. Júlio) e confirmar que a Eva pergunta a modalidade, oferece 1 hora e informa R$ 700,00.

## Fora do escopo deste PR

Dois defeitos de dados apareceram na mesma conversa e alimentaram a confusão, mas não são causa dos bugs acima e ficam para um PR próprio:

- `is_patient` virou `True` no passo 6, embora quem esteja no WhatsApp seja a mãe.
- `guardian_name` foi capturado como a frase *"Por gentileza, veja se ele consegue atender na quinta-feira"*, e depois sobrescrito com o nome da própria paciente.

O caso da Beatriz está sendo tratado manualmente com a paciente.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
