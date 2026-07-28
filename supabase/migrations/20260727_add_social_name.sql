-- Migration: add social_name (nome social) column to patients table

ALTER TABLE patients
  ADD COLUMN IF NOT EXISTS social_name TEXT NULL;
