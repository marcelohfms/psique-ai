# Design: Contatos não-paciente (silenciamento do bot)

**Data:** 2026-06-23
**Status:** Aprovado

## Contexto

Alguns números que escrevem para a clínica não são pacientes: representantes de laboratório, médicos parceiros, fornecedores, funcionários. Hoje o bot trata qualquer número desconhecido como paciente novo e inicia o cadastro (`collect_info`) — ou seja, ficaria perguntando "qual seu nome? você já é paciente?" para um representante.

A equipe já marca essas conversas no Chatwoot com a label **`eva-inativa`** (Eva é o nome do bot) + uma segunda label de categoria (`médico`, `representante`, etc.). A label `eva-inativa` é o **gatilho de silêncio**; a segunda label é apenas o motivo, para organização da equipe (o bot a ignora).

Além disso, esses contatos foram migrados como **pacientes comuns** na tabela `patients` (a informação da label vive só no Chatwoot, não no banco), poluindo a base.

## Objetivo

1. **Runtime:** o bot fica em **silêncio** (não responde, não cadastra) quando a conversa deve ser tratada por humano.
2. **Dados:** representar esses contatos como **contatos soltos** (sem vínculo a nenhum paciente), marcados como não-paciente no próprio banco.

Comportamento desejado: **apenas silêncio** (nenhuma ação ativa de handoff — um humano já aplicou a label e está cuidando).

## Dois mecanismos de silêncio (complementares)

1. **Label `eva-inativa` (Chatwoot)** — fonte viva, controlada pela equipe. Cala o bot para qualquer conversa marcada, inclusive números desconhecidos que não estão no banco.
2. **`manual_hold = true` no contato (banco)** — robusto para contatos já reconciliados; o banco "sabe" que é não-paciente e não depende da label estar aplicada.

Qualquer um dos dois silencia o bot. Reutilizamos a coluna **`contacts.manual_hold`** já existente (o bot já silencia com base nela) — não criamos coluna nova.

## Parte 1 — Silêncio em runtime

**Onde:** em `app/main.py`, no início do processamento de uma mensagem recebida, **antes** de qualquer resolução de paciente/onboarding (antes do `get_user_by_phone`).

**Fluxo:**
1. Extrai as labels da conversa: do payload do webhook (`conversation.labels`) se presente; senão, chama `chatwoot.get_labels(conversation_id)`.
2. Se `eva-inativa` ∈ labels → **return** (silêncio).
3. Busca o contato: `get_contact_by_phone(phone)`. Se `contact.manual_hold` for `true` → **return** (silêncio).
4. Caso contrário → segue o fluxo normal.

**Componentes:**
- `app/chatwoot.py`: novo helper `get_labels(conversation_id) -> set[str]` (extrair o GET que o `set_labels` já faz internamente, expondo de forma reutilizável).
- `app/main.py`: adicionar os dois checks (label + manual_hold-do-contato) logo no início, retornando cedo — espelhando o padrão atual do `manual_hold`.

**Detalhe crítico — manual_hold em contato solto:** o check atual de `manual_hold` (`app/main.py`) itera sobre os **pacientes** retornados por `get_users_by_phone`. Um contato solto **não tem paciente** → a lista vem vazia → o `manual_hold` do contato não seria visto. Por isso o novo check precisa ler o `manual_hold` **direto do contato** (`get_contact_by_phone`), e não apenas via os pacientes. O check legado (via pacientes) permanece para os casos de paciente com manual_hold.

## Parte 2 — Reconciliação dos dados (script único)

Transformar os representantes em **contatos soltos com `manual_hold=true`** (sem vínculo a paciente).

**Representantes conhecidos (8):**

| Nome | Número (canônico) | Estado atual |
|------|-------------------|--------------|
| Tássio Medeiros | 5581997556159 | paciente no banco |
| Raísa Lima | 5581986215099 | paciente no banco |
| João Alexandre | 5581996590590 | paciente no banco |
| Emerson | 5581999940120 | paciente no banco |
| Luísa Almeida | 5581981579151 | paciente no banco (já tinha manual_hold) |
| Julio Barbosa | 5581997358795 | não está no banco |
| Joanna Lira | 5581986038837 | não está no banco |
| Morgana Araújo | 5581995293210 | não está no banco |

**Procedimento por representante:**
- **Se está no banco como paciente:** apagar o registro de `patients` + os vínculos `patient_contacts`, **mantendo o `contact`**; setar `contact.manual_hold = true`. Guarda: só apagar o paciente se o contato não estiver vinculado a um paciente real distinto.
- **Se não está no banco:** criar um `contact` solto (número canônico) com `manual_hold = true`.

Nenhum dos 5 que estão no banco tem agendamento — remoção do paciente é segura, sem perda de dado clínico.

O script roda em modo dry-run primeiro (imprime o que faria), com aval antes da execução real — mesmo padrão da deduplicação.

## Tratamento de erros

- `get_labels`: se a chamada ao Chatwoot falhar, não bloquear o fluxo — tratar como "sem label" e seguir (o `manual_hold` ainda protege os contatos reconciliados). Erro é logado, não propagado.
- `get_contact_by_phone`: se falhar, seguir o fluxo normal (degradação graciosa); a label ainda pode silenciar.

## Testes

- `tests/test_webhook.py` (ou `test_process_message.py`): mensagem de conversa com label `eva-inativa` → bot não processa (mock de `get_labels`/payload), nenhuma resposta enviada, nenhum onboarding.
- Mensagem de contato solto com `manual_hold=true` → bot silencia (mock de `get_contact_by_phone`).
- Mensagem sem label e sem manual_hold → fluxo normal segue (regressão).
- `tests/test_chatwoot.py`: `get_labels` parseia corretamente a resposta da API.

## Fora de escopo

- Categorização por tipo (médico/fornecedor/funcionário) no banco — a segunda label do Chatwoot já cobre, e o bot a ignora. Não criamos coluna `kind`.
- Ação ativa de handoff (desatribuir bot, nota interna) — decidido: apenas silêncio.
- Aplicar a label `eva-inativa` proativamente via API — a equipe aplica manualmente; o `manual_hold` cobre os 8 reconciliados independente da label.
