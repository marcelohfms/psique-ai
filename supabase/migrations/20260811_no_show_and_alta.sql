-- Desfechos de consulta: 'alta' (return_reminders) e mensagem de falta.
-- 1) return_interval passa a aceitar o sentinela 'alta' (paciente recebeu
--    alta: sem próximo retorno). 2) next_return_date deixa de ser NOT NULL
--    (alta não tem data de retorno). 3) appointments ganha flag de envio da
--    mensagem de falta. appointments.status NÃO tem CHECK no repo — 'no_show'
--    não precisa de alteração de constraint.

DO $$
DECLARE
    constraint_name text;
BEGIN
    SELECT con.conname INTO constraint_name
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'return_reminders'
      AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%return_interval%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE return_reminders DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE return_reminders ADD CONSTRAINT return_reminders_return_interval_check
    CHECK (return_interval IN ('15_dias','1_mes','2_meses','3_meses','4_meses','6_meses','alta'));

ALTER TABLE return_reminders ALTER COLUMN next_return_date DROP NOT NULL;

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS no_show_message_sent_at TIMESTAMPTZ;
