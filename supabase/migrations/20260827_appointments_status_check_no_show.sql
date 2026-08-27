-- appointments_status_check passa a aceitar 'no_show'.
--
-- A migration 20260811_no_show_and_alta.sql assumiu que appointments.status
-- não tinha CHECK ("NÃO tem CHECK no repo") — mas em prod a constraint
-- appointments_status_check existe desde a criação da tabela, com
-- ('scheduled','completed','canceled','blocked','pending_reschedule').
-- Resultado: todo UPDATE para status='no_show' falhava com 23514, e os
-- botões "Não compareceu" (painel da atendente e dashboard) nunca
-- funcionaram em prod. Incidente de 27/08/2026 (paciente Aquiles).
--
-- Aplicada manualmente em prod em 27/08/2026
-- (scripts/_fix_appointments_status_check.py).

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'appointments'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%status%'
      AND pg_get_constraintdef(con.oid) NOT LIKE '%reschedule_initiated_by%'
      AND pg_get_constraintdef(con.oid) NOT LIKE '%consultation_type%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE appointments DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE appointments ADD CONSTRAINT appointments_status_check
    CHECK (status IN ('scheduled','completed','canceled','blocked',
                      'pending_reschedule','no_show'));
