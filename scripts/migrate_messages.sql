CREATE TABLE IF NOT EXISTS messages (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    phone      TEXT        NOT NULL,
    role       TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT        NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_phone_idx       ON messages (phone);
CREATE INDEX IF NOT EXISTS messages_created_at_idx  ON messages (created_at);
