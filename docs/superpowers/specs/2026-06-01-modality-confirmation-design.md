# Design: Confirmação obrigatória de modalidade (presencial/online)

**Data:** 2026-06-01  
**Status:** Aprovado

## Problema

Eva só perguntava presencial/online quando o slot do calendário indicava `"escolha"` ou `"presencial_sob_consulta"`. Para slots `"apenas online"` ela informava e seguia. Não havia nenhuma pergunta obrigatória para pacientes sem restrição — o comportamento dependia inteiramente da classificação do slot.

## Objetivo

Eva deve **sempre** perguntar ao paciente se a consulta será presencial ou online antes de confirmar o agendamento, exceto:

1. O slot é `"apenas online"` no calendário (não há escolha possível), OU
2. O paciente tem `modality_restriction` preenchido no banco (restrição cadastral).

## Campo no banco

Tabela `users`, coluna `modality_restriction`:

| Valor | Significado |
|-------|-------------|
| `"online"` | Paciente atende exclusivamente online |
| `"presencial"` | Paciente atende exclusivamente presencial |
| `NULL` | Sem restrição — paciente pode escolher |

## Regra de modalidade (nova lógica unificada)

Após o paciente escolher um horário, Eva aplica esta ordem de prioridade:

1. **`modality_restriction` preenchido** → usar a restrição, NÃO perguntar. Informar ao paciente: _"Conforme seu cadastro, sua consulta será [online/presencial]."_
2. **Slot `"apenas online"`** → definir `modality="online"`, NÃO perguntar. Informar que o horário é exclusivamente online.
3. **Qualquer outro caso** (`"escolha"` ou `"presencial_sob_consulta"`) → **sempre perguntar** antes de chamar `confirm_appointment`.

A lógica de `"presencial_sob_consulta"` permanece inalterada: se o paciente escolhe presencial nesse tipo de slot, Eva chama `transfer_to_human` (exceto quando for instrução da atendente).

## Mudanças no código

### 1. `app/graph/state.py`
Adicionar campo ao `ConversationState`:
```python
modality_restriction: Literal["online", "presencial"] | None
```

### 2. `app/graph/nodes.py` — `collect_info_node`
Ao carregar dados do usuário do banco, ler `modality_restriction` e salvar no state.

### 3. `app/graph/prompts.py`
Substituir a seção "MODALIDADE DE ATENDIMENTO" em ambos os prompts (`RETURNING_PATIENT_SYSTEM` e `NEW_PATIENT_SYSTEM`) pela nova regra unificada acima.

### 4. `app/graph/tools.py` — `confirm_appointment` e `reschedule_appointment`
No bloco de `effective_modality`, adicionar verificação do state/DB: se `modality_restriction` estiver preenchido, sobrescrever o valor antes de qualquer outra lógica.

## Testes

- Paciente com `modality_restriction="online"` → Eva não pergunta, agenda como online.
- Paciente com `modality_restriction="presencial"` → Eva não pergunta, agenda como presencial.
- Paciente sem restrição, slot `"escolha"` → Eva sempre pergunta.
- Paciente sem restrição, slot `"apenas online"` → Eva informa e agenda como online, sem perguntar.
- Paciente sem restrição, slot `"presencial_sob_consulta"`, escolhe presencial → Eva chama `transfer_to_human`.

## Fora de escopo

- Criação/edição do campo `modality_restriction` pela Eva (atendente gerencia via Supabase).
- Alteração na estrutura do banco (campo já existe).
