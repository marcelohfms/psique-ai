# Cadastro: solicitar dados cadastrais apenas para novos pacientes

**Data:** 2026-06-25
**Status:** Aprovado (aguardando plano de implementação)

## Problema

Hoje a Eva (FASE 2 — cadastro) coleta o conjunto completo de dados cadastrais
de **todos** os pacientes antes de agendar, incluindo quem **já é paciente da
clínica**. A pergunta `is_returning_patient` só acontece no passo 9, depois de já
ter pedido data de nascimento, CPF do paciente e dados do responsável. Isso faz a
clínica recoletar dados que já existem no cadastro de pacientes antigos.

## Objetivo

Para pacientes que já são da clínica (`is_returning_patient=true`), coletar apenas
o mínimo necessário para identificá-los e agendar, pulando os dados cadastrais que
já existem no sistema.

## Mínimo por perfil

- **Paciente novo** (`is_returning_patient=false`): cadastro completo, como hoje.
- **Paciente antigo** (`is_returning_patient=true`): no mínimo **nome completo +
  data de nascimento + e-mail**, mais médico preferido e dia/turno do agendamento.
- **Paciente antigo menor de 18 anos**: além do mínimo acima, confirmar **relação
  e nome do responsável** que está em contato — mas **não** pedir CPF do responsável.

## Parte 1 — Fluxo de perguntas (`app/graph/prompts.py`)

Antecipar a pergunta `is_returning_patient` para logo após conhecer o paciente e
a idade, e ramificar a coleta.

Nova ordem das perguntas:

1. `user_name`
2. `is_patient` (a consulta é para a própria pessoa ou outra)
3. `patient_name`
4. `birth_date` (a idade é calculada → define se é menor de 18)
5. `is_returning_patient` — "Você já é paciente da clínica?" *(movido para cá)*

Ramificação após o passo 5:

| Campo                      | Novo paciente | Já é paciente |
|----------------------------|:-------------:|:-------------:|
| `patient_cpf`              | pergunta      | **pula**      |
| `guardian_relationship` (menor) | pergunta | pergunta      |
| `guardian_name` (menor)    | pergunta      | pergunta      |
| `guardian_cpf` (menor)     | pergunta      | **pula**      |
| `preferred_doctor`         | pergunta      | pergunta      |
| `patient_email`            | pergunta      | pergunta      |
| `consultation_reason`      | pergunta      | pula (já hoje)|
| `referral_professional`    | pergunta      | pula (já hoje)|

Observações:
- `consultation_reason` e `referral_professional` já são exclusivos de novos
  pacientes hoje; permanecem assim.
- A regra "CRÍTICO — MENORES DE IDADE" do prompt, que hoje exige `guardian_cpf`
  para qualquer menor, precisa passar a exigir `guardian_cpf` somente para menores
  que **não** são pacientes antigos. Para menor antigo, exigir apenas
  `guardian_name` + `guardian_relationship`.

## Parte 2 — Lógica de cadastro completo (`app/database.py::is_registration_complete`)

Afrouxar a exigência de `guardian_cpf`:

- Campos universais permanecem obrigatórios para todos: `name`, `email`,
  `birth_date`, `doctor_id`, `is_patient` (não-None), `is_returning_patient`
  (não-None); e `patient_name` quando `is_patient=false`.
- Para menores (`age < 18`): `guardian_name` e `guardian_relationship` continuam
  obrigatórios sempre.
- `guardian_cpf` passa a ser obrigatório **apenas** quando `age < 18` **e**
  `is_returning_patient` é `false` (paciente novo). Para menor antigo, não bloqueia.
- `patient_cpf` continua fora da função (nunca foi bloqueante).

Isso mantém o `_route_entry` em `app/graph/graph.py` coerente: um paciente antigo
sem CPF do responsável não será mais barrado e devolvido ao `collect_info`.

## Testes

- `tests/test_process_message.py` — atualizar/adicionar cenários de coleta:
  - nova ordem (pergunta "já é paciente?" cedo);
  - paciente antigo não recebe pergunta de CPF do paciente;
  - menor antigo recebe relação/nome do responsável, mas não CPF do responsável.
- Testes de `is_registration_complete` (em `tests/test_tools.py` ou equivalente):
  - menor antigo sem `guardian_cpf` → completo;
  - menor novo sem `guardian_cpf` → incompleto (regressão preservada);
  - paciente antigo adulto sem `patient_cpf` → completo.

## Fora de escopo

- Buscar/casar automaticamente o registro do paciente antigo no banco a partir do
  nome/nascimento (continua sendo feito pela resolução por telefone existente).
- Qualquer alteração na coleta para novos pacientes além da reordenação.
