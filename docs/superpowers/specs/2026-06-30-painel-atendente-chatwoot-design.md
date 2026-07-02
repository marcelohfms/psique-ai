# Painel da Atendente no Chatwoot — Design

**Data:** 2026-06-30
**Status:** Aprovado para planejamento

## Problema

A Eva (chatbot LangGraph) às vezes entende errado dados do paciente (é paciente ou não, médico preferido, retornante, data de nascimento) ou registra agendamentos com informação incorreta. Hoje a atendente não tem como corrigir isso sem rodar scripts manuais. Precisamos de um painel onde ela:

1. **Edite dados direto no banco** (cadastro de paciente/contato, flags clínicas, agendamentos, pagamentos).
2. **Resete o checkpoint da Eva** com um clique, para que a Eva "esqueça" o entendimento errado e releia os dados corrigidos do banco.

O painel é embutido na conversa do Chatwoot que a atendente já está vendo.

## Decisões de design (travadas)

- **Onde construir:** estender o app `dashboard/` standalone que já existe. O app do bot (`app/`) **não é tocado** (sem mudanças de código nele).
- **Edição direta:** os formulários gravam direto nas tabelas (`patients`/`contacts`/`patient_contacts`/`appointments`), **sem** aplicar as regras de negócio da Eva (ex.: "menor exige responsável"). A atendente é responsável pelo que digita.
- **Checkpoint:** um único botão **"Resetar checkpoint"** que apaga as linhas de `checkpoints`/`checkpoint_writes`/`checkpoint_blobs` daquele telefone. **Não** sincroniza/edita campo a campo. Preserva o histórico de mensagens (`messages`).
- **Modelo de dados:** produção já está em `patients`/`contacts`/`patient_contacts` (não em `users`).
- **Surface:** Chatwoot Dashboard App (iframe) dentro da conversa.

## Arquitetura

```
Chatwoot (iframe Dashboard App)
        │  postMessage → phone_number do contato da conversa
        ▼
dashboard/ (FastAPI, projeto separado, Supabase + Google API direto)
        │
        ├─ resolve paciente(s) pelo telefone (variantes com/sem 9)
        ├─ formulários → escreve direto nas tabelas
        ├─ Calendar (Fases 2–3) → importa app/google_calendar
        └─ botão Resetar → DELETE nas 3 tabelas de checkpoint por thread_id
        ▼
Postgres / Supabase (mesmo banco do bot)
```

**Reuso de código vs. isolamento de deploy:** o `dashboard/` tem Dockerfile próprio com `COPY . .` a partir do contexto `dashboard/` — ou seja, **`app/` NÃO está na imagem em runtime**. Por isso:
- **Fase 1 (esta entrega):** o dashboard **replica** as poucas queries necessárias (resolução de contato/paciente por telefone, variantes com/sem 9) num módulo próprio (`dashboard/attendant_db.py`), usando o cliente Supabase que o dashboard já tem. Sem importar `app/`. Autocontido.
- **Fases 2–3 (Calendar):** reusar `app.google_calendar` exigirá tornar `app/` disponível para a imagem do dashboard (mudar o contexto/Dockerfile do build, ou vendorizar o módulo, ou expor um endpoint no bot). **Decisão adiada para o início da Fase 2.**

**Em nenhum caso o grafo (`app.graph`) é importado.**

> Nota: o reset do checkpoint é apenas `DELETE ... WHERE thread_id = <phone>` nas 3 tabelas — não precisa do grafo vivo. É por isso que o painel pode morar no dashboard standalone. O próprio bot já faz esse delete em `app/main.py:_reset_conversation`.

## Identificação do paciente (Chatwoot → painel)

1. O Dashboard App é configurado no Chatwoot apontando para a URL do dashboard (ex.: `/atendente?token=SEGREDO`).
2. O front do iframe pede o contexto com `window.parent.postMessage('chatwoot-dashboard-app:fetch-info', '*')` e recebe um evento `message` com `event: 'appContext'` contendo `data.contact.phone_number`.
3. O backend resolve o **contato** pelo telefone (variantes com/sem 9 via `app.patients`) e lista os **pacientes vinculados** (`get_patients_by_contact`).
4. Se houver mais de um paciente no mesmo número (responsável com vários filhos), o painel mostra um **seletor** antes dos formulários. O reset de checkpoint é **por telefone** (uma conversa), independente do paciente selecionado.

## Autenticação

Token secreto na URL do iframe (`?token=...`), validado no backend, em vez do HTTP Basic atual (que abriria um popup de senha dentro do iframe). O segredo vem de env (ex.: `ATTENDANT_PANEL_TOKEN`). As rotas existentes do dashboard mantêm o HTTP Basic; as novas rotas do painel usam o token.

## Auditoria

Cada edição e cada reset gravam um registro na tabela `events` (já existe): `event_type` (ex.: `attendant_edit_patient`, `attendant_reset_checkpoint`), `phone`, e `metadata` com o diff/contexto.

---

## Fases de construção

Cada fase entrega valor isoladamente e vira um plano de implementação próprio.

### Fase 1 — Núcleo (cadastro + flags + resetar checkpoint)

Escopo desta entrega. Sem Google Calendar — só Supabase.

**Backend (novas rotas em `dashboard/main.py` ou módulo novo):**
- `GET /atendente?token=...` — página do painel (HTML/template novo). Renderiza shell; o resto carrega via JS a partir do telefone recebido do Chatwoot.
- `GET /api/atendente/resolve?phone=...&token=...` — retorna contato + lista de pacientes vinculados (para o seletor).
- `GET /api/atendente/paciente/{patient_id}?token=...` — dados completos do paciente + vínculo `patient_contacts`.
- `POST /api/atendente/contato/{contact_id}?token=...` — atualiza `contacts` (nome, CPF, telefone, `active`, `manual_hold`).
- `POST /api/atendente/paciente/{patient_id}?token=...` — atualiza `patients` (todos os campos abaixo).
- `POST /api/atendente/vinculo/{pc_id}?token=...` — atualiza `patient_contacts` (role, is_self, relationship).
- `POST /api/atendente/reset-checkpoint?phone=...&token=...` — DELETE nas 3 tabelas de checkpoint por `thread_id`; grava `event`.

**Campos editáveis:**
- **Contato (`contacts`):** name, cpf, phone, active, manual_hold.
- **Paciente (`patients`):** name, birth_date (texto dd/mm/aaaa), patient_cpf, email, doctor_id (Dr. Júlio / Dra. Bruna), is_returning_patient, modality_restriction (online/presencial/—), age_exception, custom_price, financial_name, financial_cpf, financial_email.
- **Vínculo (`patient_contacts`):** role (agendamento/financeiro/consulta), is_self, relationship (mãe/pai/tutor).

**Front:**
- Template novo (segue o estilo de `dashboard/templates/`). Lê o telefone via `postMessage` do Chatwoot; se aberto fora do Chatwoot (URL direta com `?phone=`), usa o query param para teste.
- Seletor de paciente quando houver múltiplos.
- Formulários com os campos acima; botão "Salvar" por seção.
- Botão "Resetar checkpoint" com **confirmação** (ação destrutiva da memória da conversa).

**Reset — mecânica:**
- `thread_id` é o telefone no formato completo (`55...@s.whatsapp.net`). Conferir o formato exato gravado nas tabelas de checkpoint (o bot usa `phone` com sufixo `@s.whatsapp.net` como `thread_id` — ver `app/main.py`). O reset deve casar esse formato, não o telefone "stripado".
- DELETE em `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` por `thread_id`, cada um em try/except (igual ao bot).
- **Não** apaga `messages` nem o registro do paciente.

### Fase 2 — Corrigir agendamentos (com Calendar)

- Listar agendamentos do paciente (`appointments` por `patient_id`).
- **Cancelar:** `cancel_event(calendar_id, appointment_id)` + atualizar `status` no banco. (`appointment_id` É o event_id do Calendar — ver `app/graph/tools.py`.)
- **Remarcar:** `update_event` (mesmo event_id, novo horário) ou recriar evento, atualizando o banco — espelhar a lógica de remarcação em `app/graph/tools.py`.
- **Editar:** status, consultation_type, modality direto no banco.
- Importa `app.google_calendar`; precisa do mapeamento médico → `calendar_id`.

### Fase 3 — Agendamento novo (com Calendar)

- Mostrar slots livres (`get_available_slots`) por médico/dia/turno/duração.
- Reservar: `create_event` + `INSERT` em `appointments` (espelhar `confirm_appointment` em `tools.py`, inclusive `patient_id`/`contact_id`).
- Regras de duração (menor de 18 = 2h na primeira consulta com Dr. Júlio; Dra. Bruna sempre 1h) — reusar de `app/google_calendar.py`.

### Fase 4 — Embed no Chatwoot

- Configurar o Dashboard App no Chatwoot (URL + token).
- Garantir o handshake `postMessage` e o CSP/headers do iframe (o dashboard precisa permitir ser embutido pelo domínio do Chatwoot — `X-Frame-Options`/`frame-ancestors`).
- Testar o fluxo ponta a ponta dentro de uma conversa real.

## Testes

Seguindo o `CLAUDE.md`: o `dashboard/` hoje não tem suite própria. Para a Fase 1, adicionar testes mockados (Supabase mockado) cobrindo:
- Resolução de paciente por telefone (com variante de 9).
- Cada endpoint de update grava os campos certos.
- Reset apaga as 3 tabelas pelo `thread_id` no formato correto e grava o `event`.
- Validação do token (401 sem token / token errado).

## Fora de escopo (YAGNI)

- Sincronização campo-a-campo do checkpoint (decidido: só reset).
- Botão de "zerar conversa" completo (apagar mensagens/paciente) — o reset cirúrgico basta.
- Aplicação das regras de negócio da Eva nos formulários (edição é direta).
- Consolidar o `dashboard/` dentro do `app/` do bot.

## Riscos / pontos de atenção

- **Formato do `thread_id`:** confirmar com o dado real qual string o checkpointer usa como `thread_id` antes de implementar o reset, senão o DELETE não casa nada.
- **Resetar durante processamento:** se a Eva estiver processando uma mensagem no exato momento do reset, pode haver corrida. Baixa probabilidade; aceitável para v1.
- **Footgun de telefone:** sempre resolver contato pelas variantes com/sem o 9 (reusar `app.patients`).
- **CSP do iframe:** o dashboard precisa liberar embed pelo domínio do Chatwoot (Fase 4).
- **Edição direta sem validação:** pode gerar estados que a Eva não esperava (ex.: menor sem responsável). Aceito por decisão; auditoria em `events` ajuda a rastrear.
