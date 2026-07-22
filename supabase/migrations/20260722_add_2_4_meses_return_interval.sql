-- Adiciona '2_meses' e '4_meses' aos valores válidos de return_interval.
-- Busca o nome real da constraint (não confia no nome auto-gerado padrão)
-- pra funcionar independente de como o Postgres nomeou ela.

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
    CHECK (return_interval IN ('15_dias','1_mes','2_meses','3_meses','4_meses','6_meses'));
