-- Tracks whether the patient is already a returning patient of the clinic.
-- Set during onboarding when the contact answers "O paciente já é paciente da clínica?".
-- Used at booking time to determine consultation_type (primeira_consulta vs acompanhamento)
-- for minor patients with Dr. Júlio, where pricing differs between first and follow-up visits.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_returning_patient BOOLEAN;
