-- Registra quem pediu o reagendamento em andamento (paciente ou clínica/médico),
-- para que reschedule_appointment saiba como classificar o evento
-- appointment_rescheduled e evitar que um reagendamento por iniciativa da
-- clínica consuma a "1ª remarcação grátis" do paciente.
ALTER TABLE appointments
  ADD COLUMN IF NOT EXISTS reschedule_initiated_by text
    CHECK (reschedule_initiated_by IN ('patient', 'clinic'));
