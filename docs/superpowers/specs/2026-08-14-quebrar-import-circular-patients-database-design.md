# Quebrar o import circular entre `app/patients.py` e `app/database.py`

**Data:** 2026-08-14
**Status:** Aprovado para planejamento

## Problema

O workflow **Send payment reminders** falha em 100% das execuções desde
2026-08-12 17:26Z. O script morre no import, antes de qualquer lógica:

```
File "scripts/send_payment_reminders.py", line 30
    from app.patients import get_contact_by_id
File "app/patients.py", line 9
    from app.database import get_supabase
File "app/database.py", line 594
    from app.patients import (...)
ImportError: cannot import name 'get_contact_by_phone' from partially
initialized module 'app.patients' (most likely due to a circular import)
```

Consequência operacional: há ~2 dias nenhum lembrete de taxa de reserva é
enviado e nenhuma consulta não paga é cancelada.

### Causa raiz

Existe um ciclo `patients ⇄ database` desde
[#71](docs/superpowers/specs/2026-06-15-patients-contacts-schema-design.md)
(2026-06-23). Ele era **latente**: `database.py` importa de `app.patients` no
**fim** do módulo justamente para tolerar o ciclo, e isso funciona *desde que
`app.database` seja carregado primeiro*. Se `app.patients` entra primeiro, ele
para na linha 9, chama `database.py`, que na linha 594 tenta importar de um
`app.patients` ainda pela metade → `ImportError`.

O commit `4e60cae` ([#152](https://github.com/marcelohfms/psique-ai/pull/152))
adicionou `from app.patients import get_contact_by_id` no topo de
`scripts/send_payment_reminders.py` **sem** o guard que os outros crons têm:

```python
import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
```

Esse guard existe hoje em `send_appointment_reminders.py`,
`complete_appointments.py`, `send_no_show_messages.py` e
`send_return_reminders.py`. É um remendo por entrypoint: **todo script novo que
importe `app.patients` primeiro quebra do mesmo jeito**, e o CI não pega porque
os testes importam os módulos em outra ordem.

## Mapa das dependências (levantado, não presumido)

### `patients.py` → `database.py` — a aresta que fecha o ciclo

| Símbolo | Uso |
|---|---|
| `get_supabase` | import no topo (linha 9), 13 chamadas |
| `_phone_variants` | import lazy dentro da função (linha 42), 1 chamada |

É **só isso**. Nenhuma outra dependência.

### `database.py` → `patients.py` — dependência real de runtime

Não é re-export de compatibilidade: a API legada de `users` em `database.py` é
um **adaptador implementado em cima** do modelo novo de patients/contacts.

| Função em `database.py` | Chama de `patients.py` |
|---|---|
| `get_users_by_phone` (104) | `get_contact_by_phone` (113) |
| `upsert_user` (160) | `upsert_contact` (206), `upsert_patient` (233), `link_patient_contact` (244, 252) |
| `upsert_user` (160) | `resolve_active_patient` (import lazy, 211) |
| — (276) | import lazy adicional |

`get_patients_by_contact` aparece **apenas** na lista de import da linha 596 —
nunca é usado dentro de `database.py`. É o único re-export puro do bloco.

### Consumidores de `_strip_phone` / `_phone_variants` via `app.database`

~25 call sites: `app/graph/tools.py:15` (topo), `app/main.py` (240, 278, 721,
827, 914 — lazy), `scripts/send_payment_reminders.py:163`,
`scripts/release_pending_reschedules.py:109` e ~20 scripts one-off `_*.py`.

`_strip_phone` está **duplicado idêntico** em `app/database.py:30` e
`app/patients.py:14`.

## Arquitetura

**Princípio:** eliminar a aresta de volta extraindo o que os dois módulos
compartilham para módulos-folha. O grafo vira um DAG e o ciclo passa a ser
impossível de reintroduzir por ordem de import.

```
app/supabase_client.py        app/phone.py
  _supabase                     _strip_phone()
  get_supabase()                _phone_variants()
        ↑         ↖             ↑          ↑
        |           ╲___________|__________|
        |                       |
  app/patients.py  ◄─────  app/database.py
   (modelo novo)          (adaptador legado users)
```

Depois da mudança: `patients.py` **não importa `app.database`**. `database.py`
importa `app.patients` normalmente **no topo** — o hack de importar no fim do
módulo deixa de ser necessário.

### 1. `app/supabase_client.py` (novo, folha)

Recebe `_supabase` e `get_supabase()` de `app/database.py:15-25`, sem alteração
de comportamento. Leva junto os imports que só existem para ele: `import os` e
`from supabase import AsyncClient, acreate_client`. Nenhum import de `app.*`.

### 2. `app/phone.py` (novo, folha)

Recebe `_strip_phone()` e `_phone_variants()` de `app/database.py:30-49`, sem
alteração de comportamento. Nenhum import de `app.*`.

Dois módulos em vez de um: os helpers de telefone não têm relação com Supabase,
e já existem cópias independentes em `app/chatwoot.py`,
`dashboard/attendant_db.py` e `dashboard/payments.py` — um destino óbvio caso se
queira unificar depois (fora deste escopo).

### 3. `app/patients.py`

- Passa a importar `get_supabase`, `_strip_phone` e `_phone_variants` das
  folhas.
- Remove a definição duplicada de `_strip_phone` (linha 14).
- Remove o import lazy de `_phone_variants` (linha 42) — vira import no topo.
- **Deixa de importar `app.database`.**

### 4. `app/database.py`

- Importa `get_supabase` de `app/supabase_client.py`.
- Importa `_strip_phone`/`_phone_variants` de `app/phone.py` e **re-exporta**
  (`# noqa: F401`). Aresta para folha, não fecha ciclo. Preserva os ~25 call
  sites existentes — churn zero.
- Remove as definições locais de `_strip_phone` e `_phone_variants`.
- Remove `import os` e `from supabase import AsyncClient, acreate_client`: os
  três símbolos são usados **exclusivamente** dentro de `get_supabase()` e ficam
  órfãos após a extração (verificado por `grep`).
- Sobe o bloco da linha 594 para o topo do arquivo, junto com os imports lazy
  das linhas 211 e 276. Some o `# noqa: E402` e o comentário que explica o
  hack.
- Remove `get_patients_by_contact` da lista de import (re-export puro, não
  usado internamente).

### 5. Call sites que buscam funções de patients via `app.database`

Passam a importar de `app.patients`, a fonte canônica:

| Arquivo | Símbolo |
|---|---|
| `app/main.py:22` | `get_contact_by_phone` |
| `scripts/_link_isabela_financeiro.py:14` | `link_patient_contact` |
| `scripts/_link_fabricia_valdemar_roles.py:14` | `link_patient_contact` |
| `scripts/_link_dione_pedro_lins.py:16` | `link_patient_contact` |
| `scripts/_check_5581996571022.py:9` | `get_contact_by_phone` |

### 6. Remoção dos guards

Os 4 `import app.database  # noqa: F401` viram dead code e saem de
`send_appointment_reminders.py`, `complete_appointments.py`,
`send_no_show_messages.py` e `send_return_reminders.py`.

`scripts/send_payment_reminders.py` volta a funcionar **sem** precisar do guard
— é a validação de que a causa raiz foi removida, não remendada.

## Tratamento de erros / edge cases

- **Nenhuma mudança de comportamento em runtime.** É movimentação de símbolos;
  os corpos das funções não mudam.
- Todo o risco é em **import-time**: se um símbolo for esquecido, o erro aparece
  imediatamente no import, não silenciosamente em produção.
- `tests/test_database_shim.py` faz `patch("app.database.get_contact_by_phone")`
  e `patch("app.database.get_supabase")` — **continuam válidos**, porque os
  nomes seguem vinculados no namespace de `database.py` (o import só muda de
  posição). Mesma coisa para os patches de `app.patients.*` em
  `tests/test_patients.py` e `tests/test_tools.py`.
- `app/graph/tools.py:15` importa `_phone_variants` de `app.database` no topo —
  preservado pelo re-export.

## Testes

Arquivos existentes afetados: `tests/test_database_shim.py`,
`tests/test_patients.py`, `tests/test_tools.py` (só precisam continuar
passando — nenhum deles deve exigir edição).

**Novo — `tests/test_import_graph.py`:** é a cobertura que faltava para o CI ter
pego essa regressão.

- Para **cada** entrypoint de `scripts/` usado por workflow agendado
  (`send_payment_reminders`, `send_appointment_reminders`,
  `complete_appointments`, `send_no_show_messages`, `send_return_reminders`,
  `release_pending_reschedules`): importar o módulo em **subprocesso limpo**
  (`python -c "import ..."`, `sys.modules` vazio) e exigir exit code 0.
  Parametrizado, com env vars fake — o import não pode depender de credencial.
- Import de `app.patients` **antes** de `app.database` em subprocesso limpo →
  sucesso. Este é o teste que falha hoje e passa depois.
- Import de `app.database` antes de `app.patients` → sucesso (não regride o
  caminho que já funcionava).
- Asserção estrutural: o código-fonte de `app/patients.py` não contém
  `from app.database` nem `import app.database` — trava a aresta de volta contra
  reintrodução acidental.
- Unit dos helpers movidos em `app/phone.py`: reaproveitar os casos de
  `dashboard/tests/test_attendant_db.py` (13 dígitos, 12 dígitos, sufixo
  `@s.whatsapp.net` removido antes de variar).

## Fora de escopo

- Unificar as cópias de `_strip_phone`/`_phone_variants` em `app/chatwoot.py`,
  `dashboard/attendant_db.py` e `dashboard/payments.py`. `dashboard/` não
  importa de `app.*` e tem testes próprios.
- Concluir a migração do modelo legado `users` → `patients/contacts`. O
  adaptador em `database.py` permanece como está.
- Scripts one-off `_*.py` que não estão na tabela da seção 5.
