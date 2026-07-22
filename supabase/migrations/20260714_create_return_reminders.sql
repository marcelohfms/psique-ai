-- Cria return_reminders: rastreia a classificação de retorno periódico do
-- paciente (definida pelo médico no dashboard /retornos) e os lembretes de
-- WhatsApp já enviados no ciclo atual. Separada de `patients` de propósito —
-- patients continua focada em dado do paciente em si.

CREATE TABLE IF NOT EXISTS return_reminders (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                      UUID NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id                       UUID NOT NULL REFERENCES doctors(doctor_id),
    return_interval                 TEXT NOT NULL CHECK (return_interval IN ('15_dias','1_mes','3_meses','6_meses')),
    next_return_date                DATE NOT NULL,
    last_classified_appointment_id  UUID REFERENCES appointments(appointment_id),
    month_before_sent_at            TIMESTAMPTZ,
    month_of_sent_at                TIMESTAMPTZ,
    overdue_sent_at                 TIMESTAMPTZ,
    updated_at                      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_return_reminders_patient ON return_reminders(patient_id);

-- Mesma postura defensiva da migration 20260713 (RLS habilitado, zero
-- policies): fecha a porta pra chave anon, sem mudar o comportamento do
-- backend (que usa service_role).
ALTER TABLE IF EXISTS return_reminders ENABLE ROW LEVEL SECURITY;
