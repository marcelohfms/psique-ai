-- Habilita RLS nas tabelas de paciente que ficaram de fora da migration de
-- 2026-07-13 (20260713_enable_rls_patient_tables.sql). Mesmo padrão: RLS ligado
-- e NENHUMA policy criada de propósito — com zero policies, todo acesso via chave
-- anon é negado, enquanto a service role usada pelo backend ignora RLS. O
-- comportamento do app não muda; isso só fecha a porta para uso futuro/acidental
-- da chave anon contra essas tabelas via PostgREST.
--
-- As três tabelas de checkpoint são criadas automaticamente pelo
-- AsyncPostgresSaver do LangGraph no boot (ver app/main.py, init do checkpointer)
-- e, por nascerem fora de migration, nunca receberam RLS. São as mais sensíveis
-- do banco: guardam o estado inteiro da conversa (nome, telefone e contexto
-- clínico de todos os pacientes). O checkpointer conecta por string de conexão
-- Postgres direta (psycopg), com role privilegiada que ignora RLS, então ligar
-- RLS aqui não afeta a Eva.
--
-- `doctors` entra junto: não é dado de paciente, mas é dado interno da clínica e
-- é referenciada por FK de requests/return_reminders. Fechar a porta dela também
-- mantém o schema public consistente.
--
-- IF EXISTS: as tabelas de checkpoint podem ainda não existir num banco recém
-- criado (o setup() do checkpointer as cria no primeiro boot). O ALTER é
-- idempotente — rodar de novo depois de criadas apenas reafirma o RLS.

ALTER TABLE IF EXISTS checkpoints       ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS checkpoint_blobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS checkpoint_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS doctors           ENABLE ROW LEVEL SECURITY;
