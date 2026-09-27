-- Painel da Eva, Parte 2 (27/09/2026).
-- is_courtesy: cortesia vale só para esta consulta (antes só existia
-- patients.custom_price = 0, que vale para todas). Uma consulta é cortesia se
-- esta coluna for verdadeira OU se a ficha tiver custom_price = 0.
-- session_note: observação da sessão (ex.: "Domiciliar",
-- "1ª consulta · parte 1 de 2"). Antes ficava só no título do evento do Calendar;
-- o painel precisa dela para saber que falta marcar a 2ª parte.

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS is_courtesy BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS session_note TEXT NULL;
