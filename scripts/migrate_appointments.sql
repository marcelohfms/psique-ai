-- Recreate appointments table with UUID PK and status CHECK constraint
-- Safe to run: no production data exists yet

DROP TABLE IF EXISTS appointments;

CREATE TABLE appointments (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID        REFERENCES users(id) ON DELETE SET NULL,
    doctor_id     UUID        REFERENCES doctors(doctor_id) ON DELETE SET NULL,
    appointment_id TEXT       NOT NULL,          -- Google Calendar event ID
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'scheduled'
                              CHECK (status IN ('scheduled', 'canceled', 'completed')),
    payment_id    TEXT,                          -- reserved for future payment integration
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_appointments_user_id       ON appointments(user_id);
CREATE INDEX idx_appointments_appointment_id ON appointments(appointment_id);
CREATE INDEX idx_appointments_status        ON appointments(status);
