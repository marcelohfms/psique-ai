ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS amount_paid_cents INTEGER NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION increment_amount_paid(p_appointment_id text, p_cents integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid_cents = amount_paid_cents + p_cents
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid_cents;
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION set_amount_paid(p_appointment_id text, p_cents integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid_cents = p_cents
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid_cents;
$$ LANGUAGE sql;
