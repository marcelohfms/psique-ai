-- Separação de pacientes e contatos.
-- Cria patients, contacts, patient_contacts e estende appointments.
-- As tabelas novas convivem com `users` até o backfill e o corte do shim.

CREATE TABLE IF NOT EXISTS patients (
    id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    TEXT        NOT NULL,
    email                   TEXT,
    birth_date              TEXT,       -- formato brasileiro 'dd/mm/aaaa', igual à tabela users (não DATE)
    age                     INT,
    doctor_id               UUID        REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    is_returning_patient    BOOL,
    patient_cpf             TEXT,       -- CPF do próprio paciente
    consultation_reason     TEXT,
    referral_professional   TEXT,
    modality_restriction    TEXT        CHECK (modality_restriction IN ('online', 'presencial')),
    age_exception           BOOL        DEFAULT FALSE,
    custom_price            INTEGER,    -- valor em reais inteiros, igual à tabela users (não NUMERIC)
    booking_fee_waived      BOOL        DEFAULT FALSE,
    financial_name          TEXT,
    financial_cpf           TEXT,
    financial_email         TEXT,
    legacy_user_id          UUID,       -- rastreia a linha de origem em users (idempotência do backfill)
    created_at              TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contacts (
    id                              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone                           TEXT        UNIQUE NOT NULL,
    name                            TEXT,
    cpf                             TEXT,
    active                          BOOL        DEFAULT TRUE,
    manual_hold                     BOOL        DEFAULT FALSE,
    deactivated_at                  TIMESTAMPTZ,
    price_adjustment_notified_at    TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS patient_contacts (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id  UUID        NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    contact_id  UUID        NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role        TEXT        NOT NULL CHECK (role IN ('agendamento', 'financeiro', 'consulta')),
    is_self     BOOL        NOT NULL DEFAULT FALSE,
    relationship TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (patient_id, contact_id, role)
);

CREATE INDEX IF NOT EXISTS idx_pc_contact_role ON patient_contacts(contact_id, role);
CREATE INDEX IF NOT EXISTS idx_pc_patient_role ON patient_contacts(patient_id, role);

-- Estende appointments com patient_id/contact_id e flags que ainda não existem.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_id  UUID REFERENCES patients(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS contact_id  UUID REFERENCES contacts(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS modality TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booking_fee_paid_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_reminder_sent_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_day_before_sent_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reminder_day_of_sent_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS pos_consulta_sent_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS reschedule_requested_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS refund_requested_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS refund_completed_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS pending_reschedule BOOL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_appointments_patient_id ON appointments(patient_id);
