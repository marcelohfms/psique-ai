# Design: Separação de Pacientes e Contatos

**Data:** 2026-06-15  
**Status:** Aprovado

## Contexto

A tabela `users` atual conflate dados do **paciente** (entidade clínica) com dados do **contato** (número de WhatsApp). Isso gera gambiarras como `_SHARED_FIELDS`, lógica de fallback em cascata no `upsert_user`, e impossibilidade de associar múltiplos contatos com funções diferentes a um mesmo paciente.

O objetivo é separar essas responsabilidades em tabelas distintas e introduzir um relacionamento flexível por roles.

## Schema

### `patients` — dados clínicos do paciente

```sql
CREATE TABLE patients (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                        TEXT        NOT NULL,
    email                       TEXT,
    birth_date                  TEXT,  -- formato 'dd/mm/aaaa', idêntico à users (não DATE)
    age                         SMALLINT,  -- idêntico à users
    doctor_id                   UUID        REFERENCES doctors(doctor_id),
    is_returning_patient        BOOL,
    patient_cpf                 TEXT,  -- CPF do próprio paciente (documento do paciente)
    consultation_reason         TEXT,
    referral_professional       TEXT,
    modality_restriction        TEXT        CHECK (modality_restriction IN ('online', 'presencial')),
    age_exception               BOOL,
    custom_price                INTEGER,  -- reais inteiros, idêntico à users (não NUMERIC)
    booking_fee_waived          BOOL        DEFAULT FALSE,
    financial_name              TEXT,
    financial_cpf               TEXT,
    financial_email             TEXT,
    created_at                  TIMESTAMPTZ DEFAULT now()
);
```

### `contacts` — número de WhatsApp e estado da conversa

```sql
CREATE TABLE contacts (
    id                              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone                           TEXT        UNIQUE NOT NULL,
    name                            TEXT,
    cpf                             TEXT,        -- CPF da pessoa (paciente quando is_self, ou responsável)
    active                          BOOL        DEFAULT TRUE,
    manual_hold                     BOOL        DEFAULT FALSE,
    deactivated_at                  TIMESTAMPTZ,
    price_adjustment_notified_at    TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ DEFAULT now()
);
```

### `patient_contacts` — relacionamento com roles

```sql
CREATE TABLE patient_contacts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id  UUID        NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    contact_id  UUID        NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('agendamento', 'financeiro', 'consulta')),
    is_self     BOOL        NOT NULL DEFAULT FALSE,
    relationship TEXT,       -- relação do contato com o paciente: 'self', 'mãe', 'pai', 'tutor', etc.
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pc_contact_role  ON patient_contacts(contact_id, role);
CREATE INDEX idx_pc_patient_role  ON patient_contacts(patient_id, role);
```

**Semântica:**
- Um contato pode ter múltiplos roles para o mesmo paciente (uma linha por role)
- Um contato pode estar vinculado a múltiplos pacientes
- `is_self = TRUE` indica que o contato é o próprio paciente (não um responsável); nesse caso `relationship = 'self'`
- `relationship` descreve a relação do contato responsável com o paciente (mãe, pai, tutor...). É uma propriedade da relação, não do paciente — por isso pai e mãe podem coexistir, cada um com sua relação
- Múltiplos contatos podem ter o mesmo role para o mesmo paciente (ex: pai e mãe ambos com `agendamento`)

**Modelagem do responsável (guardião de menores):** O responsável de um paciente menor **é um contato** (linha em `contacts` com nome + `cpf`), vinculado via `patient_contacts` com `is_self = FALSE` e `relationship` preenchido. Não há colunas `guardian_*` em `patients` — isso permite múltiplos responsáveis (pai E mãe) sem duplicar dados. O CPF do responsável fica em `contacts.cpf`; o CPF do próprio paciente fica em `patients.patient_cpf`.

**Regra para menores:** um paciente com idade < 18 deve ter **ao menos um** `patient_contact` com `is_self = FALSE` (um responsável). Validado no fluxo de cadastro (equivalente ao antigo `guardian_name`/`guardian_cpf` obrigatórios em `is_registration_complete`).

**Distinção responsável vs. financeiro fiscal:** O responsável (guardião) é sempre um contato. Já o responsável financeiro *fiscal* (`patients.financial_name/cpf/email`) pode ser uma entidade puramente fiscal sem WhatsApp — por isso fica como dado plano em `patients`, não como contato.

### `appointments` — agendamentos

```sql
CREATE TABLE appointments (
    id                              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                      UUID        REFERENCES patients(id) ON DELETE SET NULL,
    contact_id                      UUID        REFERENCES contacts(id) ON DELETE SET NULL,
    doctor_id                       UUID        REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    appointment_id                  TEXT        NOT NULL,
    start_time                      TIMESTAMPTZ NOT NULL,
    end_time                        TIMESTAMPTZ NOT NULL,
    status                          TEXT        NOT NULL DEFAULT 'scheduled'
                                                CHECK (status IN ('scheduled', 'canceled', 'completed', 'pending_reschedule')),
    modality                        TEXT,
    consultation_type               TEXT        CHECK (consultation_type IN ('primeira_consulta', 'acompanhamento')),
    confirmed_at                    TIMESTAMPTZ,
    paid_at                         TIMESTAMPTZ,
    booking_fee_paid_at             TIMESTAMPTZ,
    booking_fee_waived              BOOL        DEFAULT FALSE,
    payment_reminder_sent_at        TIMESTAMPTZ,
    reminder_day_before_sent_at     TIMESTAMPTZ,
    reminder_day_of_sent_at         TIMESTAMPTZ,
    pos_consulta_sent_at            TIMESTAMPTZ,
    reschedule_requested_at         TIMESTAMPTZ,
    refund_requested_at             TIMESTAMPTZ,
    refund_completed_at             TIMESTAMPTZ,
    payment_id                      TEXT,
    pending_reschedule              BOOL        DEFAULT FALSE,
    created_at                      TIMESTAMPTZ DEFAULT now(),
    updated_at                      TIMESTAMPTZ DEFAULT now()
);
```

**Nota:** `booking_fee_waived` existe em `patients` (padrão do paciente) e em `appointments` (isenção por consulta específica).

### `messages` — histórico de mensagens

Sem alteração estrutural. Continua usando `phone` (TEXT) diretamente.

## Fluxo de resolução de contato

Ao receber mensagem de um número:

1. Busca `contact` pelo `phone`
2. Busca `patient_contacts` com `contact_id` e `role = 'agendamento'`
3. **0 pacientes** → onboarding: cria `patient`, cria (ou reutiliza) `contact`, insere linha em `patient_contacts`
4. **1 paciente** → segue direto para o fluxo do paciente
5. **2+ pacientes** → verifica agendamentos próximos; se apenas um paciente tem agendamento iminente, assume esse; caso contrário pergunta qual paciente

## Fluxo de agendamento

- `resolve_active_patient(phone)` retorna `(contact, patient)` em contexto
- O bot verifica `patient_contacts.is_self` para saber se o contato é o próprio paciente
- O `appointment` é criado com `patient_id` + `contact_id` (quem agendou)
- Lembretes e confirmações são disparados para **todos os contatos** com `role = 'agendamento'` vinculados ao `patient_id` — não apenas para quem agendou
- O primeiro contato a confirmar realiza a confirmação (idempotente via `confirmed_at`)

## Responsável financeiro vs. contato financeiro

Separação explícita:

- **Responsável financeiro** (legal/fiscal): `patients.financial_name`, `patients.financial_cpf`, `patients.financial_email` — usado para NF e imposto de renda; não necessariamente tem WhatsApp
- **Contato financeiro** (notificações): `patient_contacts` com `role = 'financeiro'` — recebe cobranças e lembretes via WhatsApp

## Migração dos dados existentes

1. Criar tabelas `patients`, `contacts`, `patient_contacts`
2. Para cada linha em `users`:
   - Inserir em `contacts` (phone, name, active, manual_hold, deactivated_at, price_adjustment_notified_at)
   - Inserir em `patients` (demais campos clínicos)
   - Inserir em `patient_contacts` com todos os 3 roles e `is_self` derivado de `users.is_patient`
3. Para phones com múltiplos `users`: criar um `contact` único e múltiplos `patients`, todos linkados
4. Atualizar `appointments.user_id` → `appointments.patient_id` + `appointments.contact_id`
5. Após validação, deprecar `users`

## Impacto no código

### `database.py`
- Remover: `get_user_by_phone`, `upsert_user`, `_SHARED_FIELDS`, lógica de fallback
- Adicionar: `get_contact_by_phone`, `get_patients_by_contact(contact_id, role)`, `upsert_patient`, `upsert_contact`, `link_patient_contact`, `resolve_active_patient(phone)`

### `app/graph/`
- Substituir chamadas a `get_user_by_phone` por `resolve_active_patient`
- Separar objeto `user` em `contact` + `patient` nos nós do grafo

### LangGraph checkpointer
- `thread_id` continua sendo o `phone` — sem alteração
