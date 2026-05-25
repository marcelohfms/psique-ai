-- Add consultation_type to appointments table.
-- Populated at booking time: 'primeira_consulta' or 'acompanhamento'.
-- Only relevant for minor patients with Dr. Júlio (different pricing tiers).
-- NULL on existing rows → register_payment defaults to acompanhamento pricing.

ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS consultation_type TEXT
        CHECK (consultation_type IN ('primeira_consulta', 'acompanhamento'));
