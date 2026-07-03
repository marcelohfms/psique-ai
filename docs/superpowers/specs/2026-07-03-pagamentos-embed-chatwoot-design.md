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
                │
                ▼
dashboard/payments.py (módulo novo, extraído de main.py)
        ├─ compute_pendencias(client, patient_ids=None)  — filtra por paciente quando informado
        └─ mark_paid(client, ...)                        — update appointments + sheet + email

dashboard/main.py
        ├─ GET  /pagamentos                  → payments.compute_pendencias(client)     (sem filtro, Basic Auth, inalterado)
        └─ POST /api/pagamentos/{id}/pagar   → payments.mark_paid(...)                  (Basic Auth, inalterado)
```

## Fluxo de dados

1. `atendente.html` já resolve `CONTACT` e `patients` (telefone → contato → pacientes vinculados) na função `load()` existente.
2. Nova chamada em paralelo: `GET /api/atendente/pagamentos?phone=...&token=...`.
3. Backend: `attendant_db.resolve_contact_and_patients(phone)` (já existe) → lista de `patient_id`s → `payments.compute_pendencias(client, patient_ids=[...])`.
4. `compute_pendencias` roda a mesma query de hoje (`appointments` com `status in (scheduled, completed)`, join `patients`/`patient_contacts`/`contacts`), mas com `.in_("patient_id", patient_ids)` quando a lista é passada.
5. Front renderiza um cartão por pendência: paciente, médico, data/hora, tipo (taxa/consulta), valor (editável), forma de pagamento (select), botão "Marcar pago".
6. "Marcar pago" → `POST /api/atendente/pagamentos/{appointment_id}/pagar` (mesmo body de `PagarBody`) → `payments.mark_paid` grava `paid_at`/`booking_fee_paid_at`, tenta gravar na planilha e enviar e-mail (mesmo comportamento best-effort de hoje — falha silenciosa com log, não quebra a resposta). Também grava em `events` via `attendant_db.log_event("attendant_pagamento_registrado", phone, {...})`, seguindo o padrão de auditoria do painel.

## Testes

Seguir o padrão de `dashboard/tests/test_attendant_routes.py` e `test_attendant_db.py`:
- `compute_pendencias` sem filtro retorna igual ao comportamento atual de `/pagamentos` (não regressão).
- `compute_pendencias` com `patient_ids` retorna só as pendências dos pacientes informados.
- `GET /api/atendente/pagamentos` sem token → 401; com token e telefone sem contato → lista vazia.
- `POST /api/atendente/pagamentos/{id}/pagar` grava `paid_at`/`booking_fee_paid_at` e chama `log_event`.
- `tests/conftest.py` (`FakeQuery`) precisa de suporte a `.in_()` para viabilizar os testes acima — hoje só tem `.eq()`.
- `tests/test_dashboard_pagamentos.py` (raiz do repo, cobre `/pagamentos` e `/api/pagamentos/.../pagar` hoje) continua passando sem alteração — é o teste de não-regressão do comportamento antigo.

## Fora de escopo (YAGNI)

- Seletor de paciente na seção de pagamentos (mostra tudo de uma vez).
- Qualquer mudança em `/pagamentos` (página cheia) além da extração para `payments.py` — comportamento idêntico.
- Configuração do Dashboard App no Chatwoot — já é escopo da Fase 4 do painel da atendente (`2026-06-30-painel-atendente-chatwoot-design.md`); esta entrega só adiciona a seção dentro do mesmo painel.

## Riscos / pontos de atenção

- `payments.py` precisa continuar sem importar `app/` (a imagem Docker do `dashboard/` não inclui `app/`) — mesma restrição já documentada para `attendant_db.py`.
- Extrair `compute_pendencias`/`mark_paid` de `main.py` sem mudar comportamento da rota `/pagamentos` existente — cobrir com o teste de não-regressão citado acima antes de mexer.
