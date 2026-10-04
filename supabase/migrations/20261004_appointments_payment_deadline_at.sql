-- Prazo da taxa de reserva prorrogado pela Eva (extend_payment_deadline).
-- Antes o prazo era guardado empurrando created_at para o futuro (prazo − 2h).
-- Isso escondia a consulta do lembrete de véspera, que só lembra consultas
-- criadas há mais de 12h (caso Davi, 04/10/2026). Nulo = prazo padrão de 2h
-- após o agendamento.

ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS payment_deadline_at TIMESTAMPTZ NULL;
