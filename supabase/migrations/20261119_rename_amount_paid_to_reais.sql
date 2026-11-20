-- Tudo numa transação: ou renomeia+converte+recria as funções, ou nada.
-- O RENAME vem primeiro e não tem IF EXISTS de propósito: uma segunda execução
-- falha aqui (a coluna amount_paid_cents já não existe) e aborta antes do
-- UPDATE, então o /100 nunca roda duas vezes. Todos os valores já foram
-- conferidos como múltiplos de 100 (zero quebrados), então a divisão é exata.
BEGIN;

ALTER TABLE appointments RENAME COLUMN amount_paid_cents TO amount_paid;

UPDATE appointments SET amount_paid = amount_paid / 100;

DROP FUNCTION IF EXISTS increment_amount_paid(text, integer);
DROP FUNCTION IF EXISTS set_amount_paid(text, integer);

CREATE FUNCTION increment_amount_paid(p_appointment_id text, p_reais integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid = amount_paid + p_reais
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid;
$$ LANGUAGE sql;

CREATE FUNCTION set_amount_paid(p_appointment_id text, p_reais integer)
RETURNS integer AS $$
  UPDATE appointments
     SET amount_paid = p_reais
   WHERE appointment_id = p_appointment_id
  RETURNING amount_paid;
$$ LANGUAGE sql;

COMMIT;
