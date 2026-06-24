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

### 1a. Label `eva-inativa` — JÁ IMPLEMENTADO (nenhum trabalho)

O silenciamento por label **já existe** em `app/main.py` e é mais completo do que o desenho original previa:
- Constantes `_EVA_INACTIVE_LABEL = "eva-inativa"` e `_EVA_ACTIVE_LABEL = "eva-ativa"`.
- No webhook, lê `payload["conversation"]["labels"]` e faz `return` (silêncio) quando `eva-inativa` está presente.
- `_handle_label_change(payload)` trata eventos de adição/remoção de label.
- `_resume_bot_for_patient(phone)` reativa quando `eva-ativa` é adicionada ou `eva-inativa` removida.

Nada a fazer aqui — apenas registrado para contexto.

### 1b. `manual_hold` em contato solto — A IMPLEMENTAR

**Problema:** o check atual de `manual_hold` em `process_message` (`app/main.py`) itera sobre os **pacientes** retornados por `get_users_by_phone`:
```python
all_users = await get_users_by_phone(phone)
if any(r.get("manual_hold") for r in all_users):
    return
```
Um **contato solto não tem paciente** → `all_users` vem vazio → o `manual_hold` do contato **não é visto**. Ou seja, setar `manual_hold=true` num contato solto hoje não silencia nada.

**Correção:** em `process_message`, além do check via pacientes, ler o `manual_hold` **direto do contato** via `get_contact_by_phone(phone)` e retornar cedo se for `true`. O check legado (via pacientes) permanece para pacientes com manual_hold.

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

- `get_contact_by_phone`: se falhar, seguir o fluxo normal (degradação graciosa); a label `eva-inativa` ainda silencia.

## Testes

- `tests/test_process_message.py`: mensagem de **contato solto com `manual_hold=true`** → `process_message` retorna cedo, nenhuma resposta/onboarding (mock de `get_contact_by_phone`).
- Mensagem de contato/paciente **sem** manual_hold → fluxo normal segue (regressão).
- O silêncio por label `eva-inativa` já tem cobertura no fluxo do webhook existente — não é alterado.

## Fora de escopo

- Categorização por tipo (médico/fornecedor/funcionário) no banco — a segunda label do Chatwoot já cobre, e o bot a ignora. Não criamos coluna `kind`.
- Ação ativa de handoff (desatribuir bot, nota interna) — decidido: apenas silêncio.
- Aplicar a label `eva-inativa` proativamente via API — a equipe aplica manualmente; o `manual_hold` cobre os 8 reconciliados independente da label.
