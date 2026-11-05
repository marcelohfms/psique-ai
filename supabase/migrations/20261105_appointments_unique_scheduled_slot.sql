-- Impede a Eva de duplicar um agendamento dela mesma.
--
-- No caso Dario Gleison (5581998196655, 09/11/2026 14:00), o replay reprocessou o
-- comprovante e, no mesmo segundo, DUAS rotas criaram consulta para o mesmo horário:
-- a reativação do comprovante (register_payment) reativou a linha cancelada e virou a
-- consulta boa e paga, e o confirm_appointment inseriu uma SEGUNDA linha para o mesmo
-- slot, essa sem taxa. A linha fantasma fez o cron mandar lembrete e cancelamento
-- equivocados a quem já tinha pago.
--
-- A trava anti-duplicata que já existe no confirm_appointment (Guard 1) lê o banco no
-- início da ferramenta e só enxerga linhas já 'scheduled', então perde a corrida quando
-- a reativação grava no mesmo instante. Este índice fecha a corrida no banco: no máximo
-- UMA consulta 'scheduled' por (patient_id, start_time). A segunda escrita concorrente
-- (INSERT do confirm_appointment ou UPDATE de reativação) falha de forma atômica com
-- 23505, e o código trata isso devolvendo "já está registrada" e roteando o pagamento
-- para a linha que venceu (ver app/graph/tools.py, _is_unique_violation).
--
-- Parcial de propósito: só 'scheduled' entra no índice, então cancelar/remarcar/concluir
-- e reusar o horário depois continua livre. start_time distingue as duas sessões de 1h
-- da primeira consulta de menor (horários diferentes), que seguem permitidas. patient_id
-- NULL não conflita (NULLs são distintos em índice único no Postgres).
--
-- Pré-condição verificada em 05/11/2026: 0 duplicatas 'scheduled' em produção.

CREATE UNIQUE INDEX IF NOT EXISTS uniq_appointments_patient_slot_scheduled
    ON appointments (patient_id, start_time)
    WHERE status = 'scheduled';
