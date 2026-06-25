# is_returning_patient obrigatório só para menores do Dr. Júlio

**Data:** 2026-06-25
**Status:** Aprovado (aguardando plano de implementação)

## Problema

`is_registration_complete` (app/database.py) hoje exige `is_returning_patient`
(não-nulo) para **todos** os pacientes. Mas esse campo só altera o atendimento em
um único caso: paciente **menor de 18 anos do Dr. Júlio**, em que a primeira
consulta é dividida em 2 momentos (2h) e tem preço diferenciado. Para todos os
demais (adultos, e menores da Dra. Bruna), ser ou não a primeira vez não muda
nada. A exigência incondicional faz cadastros legados (migrados sem o campo)
aparecerem como pendentes sem necessidade.

## Objetivo

Tornar `is_returning_patient` obrigatório **apenas** para menores do Dr. Júlio.

## Escopo

**Apenas a validação** (`is_registration_complete`). O fluxo de conversa NÃO muda:
a Eva continua perguntando "já é paciente?" como hoje (em `collect_info_node` /
`_next_question`). Esta mudança é uma rede de segurança na validação, que evita
bloquear/sinalizar como incompleto um cadastro em que o campo é irrelevante.

## Mudança

Em `is_registration_complete` (app/database.py):

- Campos universais permanecem obrigatórios para todos: `name`, `email`,
  `birth_date`, `doctor_id`, `is_patient` (não-None); e `patient_name` quando
  `is_patient=False`.
- **Remover** a exigência incondicional atual de `is_returning_patient` não-None.
- **Adicionar**: `is_returning_patient` é obrigatório (não-None) somente quando
  `age != None and age < 18 and doctor_id == DOCTOR_IDS["julio"]`.
- Bloco de menores permanece inalterado: `guardian_name` e
  `guardian_relationship` obrigatórios para todo menor; `guardian_cpf` obrigatório
  quando menor E `is_returning_patient is False`.

`DOCTOR_IDS` já está disponível no módulo `app/database.py`.

## Efeitos

- Adulto com `is_returning_patient=None` → completo (antes: incompleto).
- Menor da Dra. Bruna com `is_returning_patient=None` → completo.
- Menor do Dr. Júlio com `is_returning_patient=None` → incompleto (mantém).
- `graph._route_entry`, que reusa a mesma função, herda o comportamento — um
  cadastro legado de adulto não será mais devolvido ao `collect_info`.

## Testes

Em `tests/test_database_shim.py`:
- Ajustar `test_minor_undetermined_returning_status_is_incomplete` para usar o
  UUID real do Dr. Júlio (`DOCTOR_IDS["julio"]`) — só assim o caso é "menor do
  Júlio sem o campo" e deve continuar incompleto.
- Adicionar: adulto com `is_returning_patient=None` → completo.
- Adicionar: menor da Dra. Bruna (`doctor_id=DOCTOR_IDS["bruna"]`) com
  `is_returning_patient=None` e guardião preenchido → completo.
- Confirmar regressões existentes (menor novo sem guardian_cpf → incompleto;
  menor retornante sem guardian_cpf → completo) seguem válidas.

## Fora de escopo

- Mudar o fluxo de perguntas (a Eva continua perguntando a todos).
- Mudar a coleta de CPF/motivo/encaminhamento (feature anterior intacta).
- Qualquer alteração na lógica de preço / 2 momentos (já correta, baseada em
  `is_returning_patient` + idade + médico).
