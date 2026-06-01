-- Migration: add custom_price and booking_fee_waived to users, booking_fee_waived to appointments

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS custom_price INTEGER NULL,
  ADD COLUMN IF NOT EXISTS booking_fee_waived BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS booking_fee_waived BOOLEAN NOT NULL DEFAULT FALSE;
