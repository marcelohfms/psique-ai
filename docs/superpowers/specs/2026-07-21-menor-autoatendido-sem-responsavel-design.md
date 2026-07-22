# Menor autoatendido sem responsável cadastrado

**Data:** 2026-07-21
**Status:** Aprovado (aguardando plano de implementação)

## Problema

Pacientes menores de idade que conversam com a Eva pelo **próprio número**
(`is_patient=True` — nenhum responsável cadastrado, ex.: Clara Van Der Linden
Madruga, 16 anos, 5581999249242) ficam presos indefinidamente na pergunta
"Qual é o nome completo do responsável pelo paciente?".

Causa raiz: `_legacy_user_dict` (app/database.py:92-95) zera `guardian_name` para
`None` sempre que o contato É o paciente (`is_self=True`) — esse campo só
existe para guardar o nome do CONTATO quando ele é um terceiro (pai/mãe/etc.).
Não há onde persistir um "responsável" para quem conversa em nome próprio, então
a cada recarga do banco a resposta é apagada e a pergunta nunca se resolve.

Essa exigência aparece em **três lugares independentes** que checam o mesmo
requisito sem olhar para `is_patient`:

1. `is_registration_complete` (app/database.py:303-312) — exige `guardian_name`
   e `guardian_relationship` para todo menor, incondicionalmente. É o gate mais
   importante: decide se o cadastro está completo e libera a transição para
   `patient_agent` (onde ficam as tools como `request_document`). Mesmo que os
   outros dois pontos sejam corrigidos, esse aqui sozinho manteria a paciente
   presa.
2. `_next_question` (app/graph/nodes.py:455-458) — função de "qual a próxima
   pergunta" usada para saudação e para decidir o que perguntar após extrair
   um campo.
3. Steps 6 e 7 dentro de `collect_info_node` (app/graph/nodes.py:716-748) — o
   state machine real que efetivamente pergunta e extrai a resposta.

## Decisão

Quando quem está conversando é a própria paciente menor (`is_patient=True`),
Eva **não pergunta** nome/CPF do responsável — pula essas etapas por completo,
tanto para pacientes novos quanto para pacientes que já são da clínica.

Quando quem conversa é um terceiro (`is_patient=False` — mãe, pai, etc.), nada
muda: a exigência de guardian_name/guardian_relationship/guardian_cpf continua
como está hoje.

## Mudança

Nos três locais, adicionar a mesma condição: as checagens de
`guardian_name`/`guardian_relationship`/`guardian_cpf` só se aplicam quando
`is_patient is False` (contato é um terceiro). Quando `is_patient` é `True`
(autoatendido) ou o campo é ausente por qualquer outro motivo que não
"terceiro confirmado", os campos de responsável deixam de ser obrigatórios.

1. **`is_registration_complete`** (app/database.py): envolver o bloco
   `required_minor` (linhas 303-312) em `if user.get("is_patient") is False:`.
2. **`_next_question`** (app/graph/nodes.py): condicionar as linhas 455-458
   (`minor and not s.get("guardian_name")` / `minor and is_new and not
   s.get("guardian_cpf")`) a `s.get("is_patient") is False`.
3. **Steps 6/7 de `collect_info_node`** (app/graph/nodes.py): condicionar os
   `if` das linhas 717 e 731-733 da mesma forma.

Nenhuma mudança de schema. Nenhuma mudança em `_legacy_user_dict` (o
null-out de `guardian_name` para `is_self=True` continua correto — é
justamente por causa dele que a pergunta nunca pode ser satisfeita, e por
isso deixamos de fazê-la).

## Efeitos

- Menor autoatendido (`is_patient=True`), novo ou retornante: cadastro pode
  ficar completo sem `guardian_name`/`guardian_cpf`/`guardian_relationship`;
  Eva não pergunta essas informações; fluxo segue direto para
  médico/e-mail/solicitação de documento.
- Menor com terceiro conversando (`is_patient=False`): comportamento
  inalterado — continua exigindo e perguntando nome/CPF do responsável como
  hoje.
- Prompts que injetam `guardian_name`/`guardian_relationship` no contexto da
  LLM (app/graph/nodes.py:1462-1468, app/graph/tools.py:48-53) já têm
  fallback para "não informado" — nenhuma mudança necessária ali.

## Testes

`tests/test_database_shim.py`:
- Novo: menor autoatendido (`is_patient=True`, `guardian_name=None`,
  `guardian_relationship=None`, `guardian_cpf=None`) → `is_registration_complete`
  retorna `True` (novo e retornante).
- Confirmar que `test_minor_returning_still_requires_guardian_name_and_relationship`
  e os demais testes de menor com terceiro (`is_patient=False`) continuam
  passando sem alteração.

`tests/test_process_message.py`:
- Novo: menor autoatendido novo (`is_patient=True`, `is_returning_patient=False`,
  sem guardian_name) → próxima pergunta pula direto para médico (não pergunta
  responsável).
- Novo: menor autoatendido retornante (`is_patient=True`,
  `is_returning_patient=True`, sem guardian_name) → mesmo comportamento, pula
  direto para médico/e-mail.
- Confirmar que os testes existentes com `is_patient=False` (linhas 577, 614,
  713, 2196) continuam passando sem alteração — o fix não toca nesse caminho.

## Fora de escopo

- Qualquer mudança em `_legacy_user_dict` / roteamento de `upsert_user` para
  contato vs. paciente.
- Qualquer nova coluna em `patients` ou `contacts` para guardar um "responsável
  informal" de um menor autoatendido.
- Mudar a regra para o caso `is_patient=False` (terceiro conversando) — essa
  exigência permanece como está.
