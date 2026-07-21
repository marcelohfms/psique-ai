-- Habilita RLS (Row Level Security) nas tabelas com dado de paciente.
-- Nenhuma policy é criada de propósito: com RLS ligado e zero policies, TODO
-- acesso via chave anon é negado por padrão. O backend usa a chave service_role
-- (confirmado em 2026-07-13), que ignora RLS — então o comportamento do app não
-- muda. Isso só fecha a porta para qualquer uso futuro/acidental da chave anon
-- ou de um client novo apontando pra essas tabelas sem passar pelo backend.
--
-- `users` está incluída mesmo depreciada: ainda tem dado real de paciente sendo
-- lido/escrito hoje por 2 pontos de código ativo (ver AUDITORIA.md, grupo B).

ALTER TABLE IF EXISTS patients         ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS contacts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS patient_contacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS appointments     ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS messages         ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS documents        ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS users            ENABLE ROW LEVEL SECURITY;
