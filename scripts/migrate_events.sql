CREATE TABLE IF NOT EXISTS events (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  TEXT        NOT NULL,
    phone       TEXT,
    metadata    JSONB       DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS events_event_type_idx  ON events (event_type);
CREATE INDEX IF NOT EXISTS events_phone_idx       ON events (phone);
CREATE INDEX IF NOT EXISTS events_created_at_idx  ON events (created_at);
