-- Generic request tracking table for various clinic request types
-- Supports third-party contact requests, follow-ups, and other structured workflows
-- Each request has a phone (WhatsApp contact), optional patient context, and type-specific metadata

CREATE TABLE requests (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  type text not null CHECK (type IN ('contato_terceiro')),  -- request type enum; CHECK constraint grows with new types
  phone text not null,                   -- WhatsApp phone, every request has one
  patient_name text,                     -- patient being discussed, nullable
  doctor_id uuid references doctors(doctor_id) on delete set null,  -- responsible doctor, nullable
  content text not null,                 -- human-readable summary of request
  metadata jsonb not null default '{}'   -- type-specific fields
);

ALTER TABLE requests ENABLE ROW LEVEL SECURITY;

CREATE INDEX IF NOT EXISTS idx_requests_phone ON requests(phone);
CREATE INDEX IF NOT EXISTS idx_requests_type ON requests(type);
CREATE INDEX IF NOT EXISTS idx_requests_doctor_id ON requests(doctor_id);
