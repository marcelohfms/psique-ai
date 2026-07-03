# Pagamentos Pendentes embutido no Chatwoot — Design

**Data:** 2026-07-03
**Status:** Aprovado para planejamento

## Problema

A atendente hoje precisa abrir o dashboard em outra aba (`/pagamentos`) para ver e registrar pagamentos pendentes. Ela quer ver e registrar isso direto dentro da conversa do paciente no Chatwoot, sem sair da tela.

## Contexto: reaproveitar o painel da atendente em andamento

Existe um branch não mesclado (`feat/painel-atendente`, worktree em `.worktrees/painel-atendente`) que já implementa a infraestrutura de embutir um painel do dashboard dentro do Chatwoot: token de acesso (`ATTENDANT_PANEL_TOKEN`), rota `GET /atendente`, resolução do telefone da conversa via `postMessage` do Chatwoot, e um `APIRouter` (`dashboard/attendant_routes.py`) com auth por token. Essa entrega (Fase 1 do design `2026-06-30-painel-atendente-chatwoot-design.md`) cobre edição de cadastro e reset de checkpoint — ainda não cobre pagamentos.

**Decisão:** não criar um segundo painel/token para pagamentos. Este trabalho é uma **extensão do painel da atendente já em andamento** — adiciona uma seção "Pagamentos" na mesma página (`atendente.html`), reaproveitando o mesmo token, o mesmo iframe único no Chatwoot, e a mesma resolução de contato por telefone (`attendant_db.resolve_contact_and_patients`). Pagamentos é a prioridade imediata; as demais seções do painel (edição de cadastro, reset) seguem como já planejadas.

Implementação continua no branch `feat/painel-atendente` (worktree `.worktrees/painel-atendente`), não em um branch novo.

## Decisões de design

- **Confirmação automática ao paciente:** quando a atendente marca um pagamento como pago pelo painel, o paciente recebe automaticamente uma mensagem de confirmação no WhatsApp (mesmo tom que a Eva já usa em `register_payment`, `app/graph/tools.py:2022-2026`: *"Olá, {paciente}! 👋 Recebemos o pagamento da [taxa de reserva da sua consulta / sua consulta] com {médico}. [Sua vaga está garantida! / Obrigado!] ✅"*). Essa mensagem é enviada **só pelo caminho novo (painel embutido)** — a página cheia `/pagamentos` (Basic Auth, fora do Chatwoot) continua sem enviar mensagem, pois não tem o `conversation_id` do Chatwoot disponível.
- **Como enviar sem duplicar `app/`:** o Chatwoot já informa o `conversation_id` da conversa aberta no mesmo evento `appContext` que informa o telefone (mesmo payload usado hoje só para o telefone). O backend usa esse `conversation_id` direto — **não** precisa replicar `find_or_create_conversation` (que busca/cria contato e conversa do zero); só um `POST` simples em `/api/v1/accounts/{id}/conversations/{conversation_id}/messages` do Chatwoot, com o token do agent bot. Módulo novo e pequeno: `dashboard/chatwoot_client.py`.
- **Nunca bloqueia o registro do pagamento:** igual ao padrão já usado em `tools.py` (`except Exception: _logger.exception("PATIENT_CONFIRM FAILED...")`) — se o envio falhar (Chatwoot fora do ar, `conversation_id` ausente por algum motivo), o pagamento já foi gravado no banco e a falha só é logada.
- **Env novo no `dashboard/`:** `CHATWOOT_BASE_URL`, `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_AGENT_BOT_TOKEN` (já existem no `.env.example` na raiz, usados hoje só pelo `app/`) precisam ser configurados também no ambiente de deploy do `dashboard/` — hoje ele não usa nenhuma variável do Chatwoot.
- **Escopo dos pagamentos exibidos:** todos os pacientes vinculados ao contato/telefone da conversa (não só um paciente selecionado) — um responsável pode ter pendências de mais de um filho na mesma conversa. Sem seletor: a seção de pagamentos lista tudo de uma vez.
- **Reuso de lógica:** a query e o cálculo de valor de pendências (`dashboard/main.py`, rota `/pagamentos`) e a ação de marcar como pago (`/api/pagamentos/{id}/pagar`) são extraídos para um módulo novo `dashboard/payments.py`, usado tanto pela página cheia (`/pagamentos`, Basic Auth, sem mudança de comportamento) quanto pelas novas rotas do painel (token, filtradas por paciente).
- **Auth:** reaproveita `ATTENDANT_PANEL_TOKEN` e o padrão `verify_token` já existente em `attendant_routes.py`. Nenhuma variável de ambiente nova.
- **UI:** segue o estilo minimalista Tailwind já usado em `atendente.html` (não a folha de estilo custom de `pagamentos.html`) — cartões compactos por pendência, não tabela, já que o iframe é estreito.

## Arquitetura

```
Chatwoot (iframe único, já configurado — Fase 4 do painel da atendente)
        │ postMessage → phone_number
        ▼
dashboard/templates/atendente.html
        ├─ seção existente: Contato / Paciente / Reset
        └─ seção nova: Pagamentos
                │
                ▼
dashboard/attendant_routes.py (novas rotas, token via verify_token)
        │
        ├─ GET  /api/atendente/pagamentos          → payments.compute_pendencias(patient_ids)
        └─ POST /api/atendente/pagamentos/{id}/pagar → payments.mark_paid(...)
                                                      → chatwoot_client.send_confirmation_message(conversation_id, texto)
                │
                ▼
dashboard/payments.py (módulo novo, extraído de main.py)
        ├─ compute_pendencias(client, patient_ids=None)  — filtra por paciente quando informado
        └─ mark_paid(client, ...)                        — update appointments + sheet + email

dashboard/chatwoot_client.py (módulo novo)
        └─ send_confirmation_message(conversation_id, text) — POST direto na API do Chatwoot (agent bot token)

dashboard/main.py
        ├─ GET  /pagamentos                  → payments.compute_pendencias(client)     (sem filtro, Basic Auth, inalterado)
        └─ POST /api/pagamentos/{id}/pagar   → payments.mark_paid(...)                  (Basic Auth, inalterado)
```

## Fluxo de dados

1. `atendente.html` já resolve `CONTACT` e `patients` (telefone → contato → pacientes vinculados) na função `load()` existente. O listener de `postMessage` (`initPhone()`) passa a guardar também `data.data.conversation.id` (além do `phone_number`), num novo global `CONVERSATION_ID`.
2. Nova chamada em paralelo: `GET /api/atendente/pagamentos?phone=...&token=...`.
3. Backend: `attendant_db.resolve_contact_and_patients(phone)` (já existe) → lista de `patient_id`s → `payments.compute_pendencias(client, patient_ids=[...])`.
4. `compute_pendencias` roda a mesma query de hoje (`appointments` com `status in (scheduled, completed)`, join `patients`/`patient_contacts`/`contacts`), mas com `.in_("patient_id", patient_ids)` quando a lista é passada.
5. Front renderiza um cartão por pendência: paciente, médico, data/hora, tipo (taxa/consulta), valor (editável), forma de pagamento (select), botão "Marcar pago".
6. "Marcar pago" → `POST /api/atendente/pagamentos/{appointment_id}/pagar`, body inclui `conversation_id` (do `CONVERSATION_ID` capturado no passo 1) além dos campos de `PagarBody` de hoje. Backend:
   - `payments.mark_paid` grava `paid_at`/`booking_fee_paid_at`, tenta gravar na planilha e enviar e-mail (mesmo comportamento best-effort de hoje — falha silenciosa com log, não quebra a resposta).
   - Monta a mensagem de confirmação (texto conforme `tipo`) e chama `chatwoot_client.send_confirmation_message(conversation_id, texto)` — best-effort, mesma regra de nunca bloquear a resposta.
   - Grava em `events` via `attendant_db.log_event("attendant_pagamento_registrado", phone, {...})`, seguindo o padrão de auditoria do painel.

## Testes

Seguir o padrão de `dashboard/tests/test_attendant_routes.py` e `test_attendant_db.py`:
- `compute_pendencias` sem filtro retorna igual ao comportamento atual de `/pagamentos` (não regressão).
- `compute_pendencias` com `patient_ids` retorna só as pendências dos pacientes informados.
- `GET /api/atendente/pagamentos` sem token → 401; com token e telefone sem contato → lista vazia.
- `POST /api/atendente/pagamentos/{id}/pagar` grava `paid_at`/`booking_fee_paid_at` e chama `log_event`.
- `POST /api/atendente/pagamentos/{id}/pagar` chama `chatwoot_client.send_confirmation_message` com o `conversation_id` recebido e o texto certo por `tipo` (taxa vs. consulta) — mockar o `httpx` client.
- Falha do `send_confirmation_message` (mockar exceção) não impede a resposta 200 nem o `paid_at` gravado — cobre o comportamento best-effort.
- `tests/conftest.py` (`FakeQuery`) precisa de suporte a `.in_()` para viabilizar os testes acima — hoje só tem `.eq()`.
- `tests/test_dashboard_pagamentos.py` (raiz do repo, cobre `/pagamentos` e `/api/pagamentos/.../pagar` hoje) continua passando sem alteração — é o teste de não-regressão do comportamento antigo.

## Fora de escopo (YAGNI)

- Seletor de paciente na seção de pagamentos (mostra tudo de uma vez).
- Qualquer mudança em `/pagamentos` (página cheia) além da extração para `payments.py` — comportamento idêntico.
- Configuração do Dashboard App no Chatwoot — já é escopo da Fase 4 do painel da atendente (`2026-06-30-painel-atendente-chatwoot-design.md`); esta entrega só adiciona a seção dentro do mesmo painel.

## Riscos / pontos de atenção

- `payments.py` e `chatwoot_client.py` precisam continuar sem importar `app/` (a imagem Docker do `dashboard/` não inclui `app/`) — mesma restrição já documentada para `attendant_db.py`.
- Extrair `compute_pendencias`/`mark_paid` de `main.py` sem mudar comportamento da rota `/pagamentos` existente — cobrir com o teste de não-regressão citado acima antes de mexer.
- **Suposição a validar:** o evento `appContext` do Chatwoot inclui `data.conversation.id` junto com `data.contact` (hoje `atendente.html` só lê `contact.phone_number`). Se na prática o payload não trouxer o `conversation_id` nesse evento, a mensagem de confirmação fica indisponível até resolvermos isso (o registro do pagamento em si não é afetado, por ser best-effort) — validar assim que o Dashboard App estiver configurado num Chatwoot real (Fase 4 do painel da atendente).
- **Deploy:** lembrar de configurar `CHATWOOT_BASE_URL`/`CHATWOOT_ACCOUNT_ID`/`CHATWOOT_AGENT_BOT_TOKEN` no ambiente do `dashboard/` antes de ir pra produção — sem isso, `send_confirmation_message` falha silenciosamente (best-effort) e nenhuma mensagem sai.
