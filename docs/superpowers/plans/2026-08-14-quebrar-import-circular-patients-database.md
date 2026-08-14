# Quebrar o import circular patients/database — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar o ciclo de imports `app.patients ⇄ app.database` que derruba o cron de lembretes de pagamento em 100% das execuções desde 2026-08-12.

**Architecture:** Extrair o que os dois módulos compartilham (`get_supabase` e os helpers de telefone) para dois módulos-folha novos — `app/supabase_client.py` e `app/phone.py` — que não importam nada de `app.*`. Com isso `app/patients.py` deixa de importar `app/database.py`, a aresta de volta some e o grafo vira um DAG. Todos os call sites passam a importar cada símbolo da sua fonte canônica; nenhum re-export sobrevive.

**Distinção que guia o plano inteiro:** `app/database.py` **usa** `get_supabase` (6 chamadas) e `_strip_phone` (3 chamadas) internamente — para esses, importar da folha é uso legítimo. Já `_phone_variants` tem **zero** usos em `database.py`: é re-export puro e será eliminado (Task 5). Não marque com `# noqa: F401` um símbolo que o módulo de fato usa.

**Tech Stack:** Python 3.13, uv, pytest + pytest-asyncio, Supabase (`AsyncClient`).

**Spec:** `docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md`

## Global Constraints

- **Nenhuma mudança de comportamento em runtime.** Este plano move símbolos entre módulos. Corpos de função são copiados **verbatim**, incluindo comentários e docstrings. Se você sentir vontade de melhorar o código enquanto move, não faça — é outro PR.
- **A suíte inteira fica verde ao fim de cada task:** `uv run pytest --tb=short`. Nenhuma task pode deixar o repositório vermelho.
- **Nenhum teste pré-existente do repositório deve precisar de edição.** "Pré-existente" = os 22 arquivos já em `tests/` antes deste plano; os arquivos que este plano cria (`test_phone.py`, `test_supabase_client.py`, `test_import_graph.py`) podem crescer nas tasks seguintes, como a Task 5 faz de propósito. Os testes fazem patch no binding do módulo *consumidor* (`app.patients.get_supabase`, `app.database.get_supabase`, `app.graph.tools.get_supabase` — 159 patches no total). Como todo módulo continua usando `from X import get_supabase`, os patches seguem funcionando. **Se você se pegar editando um teste pré-existente, pare: é sinal de que o refactor mudou comportamento.**
- **Commit ao fim de cada task**, com a suíte verde.

## Estrutura de arquivos

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `app/phone.py` | **criar** — folha. Normalização de número BR: `_strip_phone`, `_phone_variants`. Zero imports de `app.*`. | 1 |
| `tests/test_phone.py` | **criar** — unit dos helpers acima. | 1 |
| `app/supabase_client.py` | **criar** — folha. Singleton do cliente Supabase: `_supabase`, `get_supabase()`. Zero imports de `app.*`. | 2 |
| `tests/test_supabase_client.py` | **criar** — unit do singleton. | 2 |
| `tests/test_import_graph.py` | **criar** — trava a regressão: importa cada entrypoint de cron em subprocesso limpo. | 3, 5 |
| `app/patients.py` | **modificar** — passa a importar das folhas; deixa de importar `app.database`. **É aqui que o ciclo quebra.** | 3 |
| `app/database.py` | **modificar** — importa das folhas; sobe os imports de `app.patients` para o topo; larga o re-export de `_phone_variants`. | 1, 2, 4, 5 |
| `app/graph/tools.py`, `app/main.py` | **modificar** — importam os helpers de telefone de `app.phone`. | 5 |
| 20 scripts one-off `_check_*.py`, `_identify_stuck_threads.py` | **modificar** — idem, reapontados para `app.phone`. | 5 |
| `app/main.py` | **modificar** — importa `get_contact_by_phone` de `app.patients`. | 6 |
| `scripts/_link_*.py`, `scripts/_check_5581996571022.py` | **modificar** — 4 scripts one-off reapontados para `app.patients`. | 6 |
| `scripts/send_*.py`, `scripts/complete_appointments.py` | **modificar** — remover os 4 guards `import app.database`. | 7 |

**Ordem das tasks importa:** as folhas nascem primeiro (1, 2), o ciclo quebra na 3, e só depois vem a limpeza (4, 5, 6, 7). Não reordene — a Task 5 depende do re-export temporário criado na Task 1 para manter a suíte verde no meio do caminho.

---

### Task 1: Extrair os helpers de telefone para `app/phone.py`

**Files:**
- Create: `app/phone.py`
- Create: `tests/test_phone.py`
- Modify: `app/database.py` (remove as definições locais nas linhas 30-49; adiciona re-export no topo)

**Interfaces:**
- Consumes: nada (primeira task).
- Produces: `app.phone._strip_phone(phone: str) -> str` e `app.phone._phone_variants(phone: str) -> list[str]`. A Task 3 importa ambos em `app/patients.py`. `app.database` continua re-exportando os dois nomes.

- [ ] **Step 1: Escrever os testes do módulo novo**

Crie `tests/test_phone.py`. Os casos vêm de `dashboard/tests/test_attendant_db.py`, que já cobre a mesma lógica numa cópia independente:

```python
from app.phone import _phone_variants, _strip_phone


def test_strip_phone_removes_whatsapp_suffix():
    assert _strip_phone("5581999998888@s.whatsapp.net") == "5581999998888"


def test_strip_phone_leaves_plain_number_untouched():
    assert _strip_phone("5581999998888") == "5581999998888"


def test_phone_variants_13_digits_returns_with_and_without_9():
    assert _phone_variants("5581999998888") == ["5581999998888", "558199998888"]


def test_phone_variants_12_digits_returns_canonical_first():
    assert _phone_variants("558199998888") == ["5581999998888", "558199998888"]


def test_phone_variants_strips_suffix_before_varying():
    assert _phone_variants("5581999998888@s.whatsapp.net") == [
        "5581999998888",
        "558199998888",
    ]


def test_phone_variants_non_brazilian_returns_single():
    assert _phone_variants("12025550123") == ["12025550123"]


def test_database_usa_o_strip_phone_compartilhado():
    """database.py chama _strip_phone 3x (linhas 313, 553, 568).

    Não testamos _phone_variants aqui: em database.py ele é re-export
    temporário, eliminado na Task 5.
    """
    from app import database, phone

    assert database._strip_phone is phone._strip_phone
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_phone.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.phone'`

- [ ] **Step 3: Criar `app/phone.py`**

Corpos copiados **verbatim** de `app/database.py:30-49` (docstring e comentários inclusive):

```python
"""Helpers de número de telefone brasileiro.

Módulo-folha: NÃO importa nada de `app.*`. Existe para que `app/database.py` e
`app/patients.py` compartilhem estes helpers sem criar um ciclo de imports.
Ver docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md
"""


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


def _phone_variants(phone: str) -> list[str]:
    """Return both the 9-digit and 8-digit variants of a Brazilian mobile number.

    Brazilian mobiles gained a leading 9 in 2012–2016. Chatwoot/Evolution may
    deliver the same number with or without the extra 9, causing duplicate users.
    We normalise to the WITH-9 form (current standard) and also try the legacy form.
    """
    digits = _strip_phone(phone)
    # Must be a Brazilian mobile: 55 + 2-digit DDD + 8 or 9 digits
    if len(digits) == 13 and digits.startswith("55"):
        # Has the 9 already (55 + DDD + 9XXXXXXXX)
        return [digits, digits[:4] + digits[5:]]   # also try without the 9
    if len(digits) == 12 and digits.startswith("55"):
        # Missing the 9 (55 + DDD + 8XXXXXXXX)
        return [digits[:4] + "9" + digits[4:], digits]  # canonical with-9 first
    return [digits]
```

- [ ] **Step 4: Apontar `app/database.py` para a folha**

Em `app/database.py`, **delete** as duas definições (linhas 30-49 no HEAD): de `def _strip_phone(phone: str) -> str:` até `return [digits]` inclusive. **Mantenha** o comentário de seção `# ── User helpers ──...` na linha 28 — ele encabeça as funções de usuário que ficam.

Logo abaixo de `from supabase import AsyncClient, acreate_client` (linha 2), adicione:

```python
# _strip_phone: uso interno (3 chamadas, linhas 313/553/568).
# _phone_variants: re-export TEMPORÁRIO — database.py não usa. Existe só para
# os 28 call sites ainda apontados para cá; a Task 5 os reaponta e remove esta
# metade do import. Não deixe passar disso.
from app.phone import _phone_variants, _strip_phone  # noqa: F401
```

- [ ] **Step 5: Rodar os testes novos**

Run: `uv run pytest tests/test_phone.py -v`
Expected: PASS (7 testes)

- [ ] **Step 6: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS. Atenção especial a `tests/test_tools.py` (importa `_phone_variants` de `app.database` no topo, em `app/graph/tools.py:15`) e `tests/test_payment_reminders_cancel.py:180`.

- [ ] **Step 7: Commit**

```bash
git add app/phone.py tests/test_phone.py app/database.py
git commit -m "refactor: extrai helpers de telefone para app/phone.py (folha)"
```

---

### Task 2: Extrair o cliente Supabase para `app/supabase_client.py`

**Files:**
- Create: `app/supabase_client.py`
- Create: `tests/test_supabase_client.py`
- Modify: `app/database.py` (remove `_supabase`/`get_supabase` das linhas 15-25 e os imports que ficam órfãos; adiciona re-export)

**Interfaces:**
- Consumes: nada da Task 1 (folhas independentes).
- Produces: `app.supabase_client.get_supabase() -> AsyncClient` (async, singleton em `app.supabase_client._supabase`). A Task 3 importa em `app/patients.py`. `app.database` continua re-exportando `get_supabase`.

- [ ] **Step 1: Escrever os testes do módulo novo**

Crie `tests/test_supabase_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

import app.supabase_client as sc


@pytest.mark.asyncio
async def test_get_supabase_cria_cliente_uma_vez_so():
    """O cliente é singleton: a segunda chamada reusa, não recria."""
    sentinel = object()
    with patch.object(sc, "_supabase", None), \
         patch.object(sc, "acreate_client", new_callable=AsyncMock,
                      return_value=sentinel) as create:
        first = await sc.get_supabase()
        second = await sc.get_supabase()

    assert first is sentinel
    assert second is sentinel
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_supabase_le_credenciais_do_ambiente():
    """URL e key vêm de env (tests/conftest.py injeta os stubs)."""
    with patch.object(sc, "_supabase", None), \
         patch.object(sc, "acreate_client", new_callable=AsyncMock) as create:
        await sc.get_supabase()

    create.assert_awaited_once_with("https://test.supabase.co", "test-key")


def test_database_still_reexports_get_supabase():
    """21 testes fazem patch("app.database.get_supabase")."""
    from app import database

    assert database.get_supabase is sc.get_supabase
```

Nota: `patch.object(sc, "_supabase", None)` restaura o valor original ao sair do
bloco — sem isso o singleton vazaria para outros testes.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_supabase_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.supabase_client'`

- [ ] **Step 3: Criar `app/supabase_client.py`**

Corpo copiado **verbatim** de `app/database.py:15-25`:

```python
"""Cliente Supabase compartilhado (singleton).

Módulo-folha: NÃO importa nada de `app.*`. Existe para que `app/database.py` e
`app/patients.py` compartilhem o cliente sem criar um ciclo de imports.
Ver docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md
"""
import os

from supabase import AsyncClient, acreate_client

_supabase: AsyncClient | None = None


async def get_supabase() -> AsyncClient:
    global _supabase
    if _supabase is None:
        _supabase = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _supabase
```

- [ ] **Step 4: Apontar `app/database.py` para a folha**

Em `app/database.py`:

1. **Delete** o bloco do cliente (linhas 13-25 no HEAD): o comentário de seção
   `# ── Supabase client ──...`, a linha `_supabase: AsyncClient | None = None` e
   a função `async def get_supabase()` inteira.
2. **Delete** as linhas 1-2, `import os` e
   `from supabase import AsyncClient, acreate_client`. Os três símbolos (`os`,
   `AsyncClient`, `acreate_client`) eram usados **exclusivamente** dentro de
   `get_supabase()` e agora ficam órfãos.
3. O topo do arquivo passa a ser:

```python
from app.phone import _phone_variants, _strip_phone  # noqa: F401
from app.supabase_client import get_supabase
```

(mantendo o comentário que a Task 1 adicionou logo acima).

**Sem `# noqa: F401` no `get_supabase`:** `database.py` o chama 6 vezes
(117, 310, 432, …). Marcá-lo como não-usado seria mentira e esconderia um
símbolo órfão de verdade no futuro.

- [ ] **Step 5: Confirmar que não sobrou órfão**

Run: `grep -nE '\bos\.|AsyncClient|acreate_client' app/database.py`
Expected: nenhuma saída. Se aparecer alguma linha, um dos símbolos ainda é usado
em `database.py` — **não delete o import correspondente**, e registre no PR.

- [ ] **Step 6: Rodar os testes novos e a suíte**

Run: `uv run pytest tests/test_supabase_client.py -v && uv run pytest --tb=short`
Expected: PASS nos dois. Atenção aos 21 testes que fazem
`patch("app.database.get_supabase")` — em `tests/test_database_shim.py` e outros.

- [ ] **Step 7: Commit**

```bash
git add app/supabase_client.py tests/test_supabase_client.py app/database.py
git commit -m "refactor: extrai cliente Supabase para app/supabase_client.py (folha)"
```

---

### Task 3: Quebrar o ciclo — `app/patients.py` passa a importar só das folhas

Esta é a task que corrige o bug. As anteriores só prepararam o terreno.

**Files:**
- Create: `tests/test_import_graph.py`
- Modify: `app/patients.py` (linha 9: troca a origem do import; linhas 14-16: remove `_strip_phone` duplicado; linha 42: remove o import lazy)

**Interfaces:**
- Consumes: `app.phone._strip_phone`, `app.phone._phone_variants` (Task 1); `app.supabase_client.get_supabase` (Task 2).
- Produces: `app/patients.py` sem nenhuma referência a `app.database`. A Task 4 depende disso para subir os imports de `database.py` ao topo.

- [ ] **Step 1: Escrever o teste de regressão**

Crie `tests/test_import_graph.py`:

```python
"""Trava a regressão de import circular entre app.patients e app.database.

Em 2026-08-12, scripts/send_payment_reminders.py passou a importar app.patients
antes de app.database e o cron quebrou em 100% das execuções por ~2 dias. A
suíte normal não pegou: o conftest e os outros testes já carregaram
app.database primeiro, então a ordem que quebra nunca acontece dentro de um
processo de teste. Por isso cada caso aqui roda num subprocesso com sys.modules
limpo.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Importar um módulo não pode depender de credencial real. Se depender, é bug
# de import-time — o valor aqui é stub de propósito.
FAKE_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-key",
    "SUPABASE_CONNECTION_STRING": "",
    "OPENAI_API_KEY": "sk-test",
    "GOOGLE_CLIENT_ID": "test-client-id",
    "GOOGLE_CLIENT_SECRET": "test-secret",
    "GOOGLE_REFRESH_TOKEN": "test-refresh-token",
    "WHATSAPP_TOKEN": "test-token",
    "WHATSAPP_PHONE_NUMBER_ID": "123456789",
    "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
    "META_APP_SECRET": "test-app-secret",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "CLINIC_NOTIFY_EMAIL": "",
}

# Todo script disparado por workflow agendado em .github/workflows/.
CRON_ENTRYPOINTS = [
    "scripts.send_payment_reminders",
    "scripts.send_appointment_reminders",
    "scripts.complete_appointments",
    "scripts.send_no_show_messages",
    "scripts.send_return_reminders",
    "scripts.release_pending_reschedules",
]


def _import_in_clean_subprocess(module: str) -> subprocess.CompletedProcess:
    """Importa `module` num interpretador novo, com sys.modules vazio.

    Os crons têm guarda `if __name__ == "__main__"`, então importar não executa
    main() — só dispara a cadeia de imports, que é o que queremos verificar.
    """
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        env={**os.environ, **FAKE_ENV},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("module", CRON_ENTRYPOINTS)
def test_cron_entrypoint_importa_em_processo_limpo(module):
    result = _import_in_clean_subprocess(module)
    assert result.returncode == 0, (
        f"{module} não importa em processo limpo:\n{result.stderr}"
    )


def test_patients_importa_sem_database_carregado():
    """A ordem exata que derrubou a produção em 2026-08-12."""
    result = _import_in_clean_subprocess("app.patients")
    assert result.returncode == 0, (
        f"app.patients não importa primeiro:\n{result.stderr}"
    )


def test_database_importa_sem_patients_carregado():
    """A ordem que já funcionava — não pode regredir."""
    result = _import_in_clean_subprocess("app.database")
    assert result.returncode == 0, (
        f"app.database não importa primeiro:\n{result.stderr}"
    )


def test_patients_nao_importa_database():
    """Trava a aresta de volta contra reintrodução acidental.

    app/patients.py deve importar apenas dos módulos-folha (app.phone,
    app.supabase_client). Se alguém reintroduzir um import de app.database
    aqui, o ciclo renasce e o próximo script que importar patients primeiro
    quebra de novo — silenciosamente, até um cron falhar em produção.
    """
    source = (REPO_ROOT / "app" / "patients.py").read_text()

    assert "from app.database import" not in source
    assert "import app.database" not in source
```

- [ ] **Step 2: Rodar e ver falhar pelo motivo certo**

Run: `uv run pytest tests/test_import_graph.py -v`
Expected: FAIL em exatamente 3 casos —
`test_cron_entrypoint_importa_em_processo_limpo[scripts.send_payment_reminders]`,
`test_patients_importa_sem_database_carregado` e `test_patients_nao_importa_database`.
O stderr dos dois primeiros deve conter `cannot import name 'get_contact_by_phone' from partially initialized module 'app.patients'`.
Os outros 5 entrypoints e `test_database_importa_sem_patients_carregado` já passam.

**Se falhar mais alguma coisa, pare e investigue antes de seguir** — significa
que as Tasks 1-2 quebraram algo.

- [ ] **Step 3: Apontar `app/patients.py` para as folhas**

Em `app/patients.py`, **substitua** a linha 9:

```python
from app.database import get_supabase
```

por:

```python
from app.phone import _phone_variants, _strip_phone
from app.supabase_client import get_supabase
```

- [ ] **Step 4: Remover o `_strip_phone` duplicado**

Ainda em `app/patients.py`, **delete** a definição local (linhas 14-16 no HEAD) —
é byte a byte idêntica à de `app/phone.py`, que agora vem pelo import:

```python
def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")
```

- [ ] **Step 5: Remover o import lazy de `_phone_variants`**

Em `get_contact_by_phone` (linha 42 no HEAD), **delete** a linha:

```python
    from app.database import _phone_variants
```

A função passa a usar o `_phone_variants` importado no topo. O corpo restante
(`client = await get_supabase()`, o `for variant in _phone_variants(phone):`)
não muda.

- [ ] **Step 6: Rodar o teste de regressão**

Run: `uv run pytest tests/test_import_graph.py -v`
Expected: PASS nos 9 casos. **O ciclo está quebrado.**

- [ ] **Step 7: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS. Os 31 `patch("app.patients.get_supabase")` de
`tests/test_patients.py` continuam valendo — `patients.py` segue usando
`from ... import get_supabase`, só mudou o módulo de origem.

- [ ] **Step 8: Commit**

```bash
git add tests/test_import_graph.py app/patients.py
git commit -m "fix: quebra o import circular patients/database

app/patients.py passa a importar das folhas (app.phone, app.supabase_client)
em vez de app.database. Some a única aresta de volta e o grafo vira DAG.

Corrige o cron de lembretes de pagamento, que falhava em 100% das execuções
desde 4e60cae (#152)."
```

---

### Task 4: Subir os imports de `app.patients` para o topo de `database.py`

Com a aresta de volta eliminada, o hack de importar no fim do módulo perde a razão de existir.

**Files:**
- Modify: `app/database.py` (remove o bloco das linhas 590-600; hoisted no topo; remove os lazy das linhas 211 e 276)

**Interfaces:**
- Consumes: `app/patients.py` sem dependência de `app.database` (Task 3).
- Produces: nada de novo — apenas reorganização. `app.database.get_contact_by_phone`, `.upsert_contact`, `.upsert_patient`, `.link_patient_contact` seguem existindo (usados internamente **e** alvo de patch em `tests/test_database_shim.py`). `app.database.get_patients_by_contact` **deixa de existir**.

- [ ] **Step 1: Confirmar que `get_patients_by_contact` não tem consumidor**

Run: `grep -rn --include='*.py' "get_patients_by_contact" . | grep -v "^./app/patients.py:" | grep -v "^./dashboard/"`
Expected: as únicas ocorrências são `app/database.py:596` (o import a remover),
`app/graph/tools.py:2758`, `scripts/_audit_*.py` e `tests/*` — e **todas** já
importam de `app.patients`, não de `app.database`. Se alguma linha importar de
`app.database`, adicione-a à Task 5 em vez de seguir.

- [ ] **Step 2: Remover o bloco do fim do arquivo**

Em `app/database.py`, **delete** o bloco inteiro (linhas 590-600 no HEAD),
comentário incluso:

```python
# ── Shim bindings (Tasks 9-10) ────────────────────────────────────────────────
# Importado no FINAL do módulo para evitar import circular: app/patients.py faz
# `from app.database import get_supabase`, então database.py precisa estar
# totalmente inicializado antes de importar de app.patients.
from app.patients import (  # noqa: E402
    get_contact_by_phone,
    get_patients_by_contact,
    upsert_contact,
    upsert_patient,
    link_patient_contact,
)
```

- [ ] **Step 3: Adicionar o import no topo**

No topo de `app/database.py`, logo abaixo do import de `app.supabase_client`:

```python
# A API legada de `users` daqui é um adaptador sobre o modelo de
# patients/contacts. get_patients_by_contact saiu da lista: era re-export puro,
# sem uso interno — quem precisa importa de app.patients.
#
# From-import (binding estático) porque os testes destes 4 fazem
# patch("app.database.X"): eles stubam a fronteira database->patients. Os nomes
# do Step 4 usam o estilo oposto, por stubarem a camada patients inteira.
from app.patients import (
    get_contact_by_phone,
    link_patient_contact,
    upsert_contact,
    upsert_patient,
)
```

Note que `get_patients_by_contact` **não** entra.

- [ ] **Step 4: Trocar os imports lazy por acesso via objeto de módulo**

Os 4 nomes importados lazy **não podem** virar `from app.patients import X` no topo. 14 patches em `tests/test_database_shim.py` fazem `patch("app.patients.X")`, e isso só surte efeito se a resolução do símbolo acontecer em **tempo de chamada**. Um `from ... import` no topo congela o binding na hora do import e o patch deixa de pegar. Não é detalhe de teste: `app/patients.py` chama `find_patient_by_name_birth` (linha 324) e `get_patient_by_id` (linha 149) internamente, e esses testes stubam a camada patients inteira, chamadas internas incluídas.

A solução que remove o import-dentro-de-função **e** preserva a semântica é referenciar pelo módulo. Acrescente ao topo, abaixo do import estático:

```python
# Acesso via objeto de módulo, não `from app.patients import X`: a resolução
# precisa acontecer em tempo de chamada para que patch("app.patients.X") pegue
# também as chamadas que patients faz a si mesmo (patients.py:149 e :324).
# Trocar por from-import quebra 14 testes em tests/test_database_shim.py.
from app import patients
```

Em `upsert_user` (linha 211 no HEAD), **delete** a linha `from app.patients import resolve_active_patient` e troque a chamada:

```python
        resolved = await patients.resolve_active_patient(phone)
```

Na função da linha 276, **delete** o bloco `from app.patients import (...)` e prefixe as três chamadas:

```python
    current = await patients.get_patient_by_id(resolved_id) if resolved_id else None
```

Faça o mesmo para as chamadas de `find_patient_by_name_birth` e `merge_duplicate_patient` nessa função: `patients.find_patient_by_name_birth(...)` e `patients.merge_duplicate_patient(...)`. São 4 call sites ao todo, um por nome.

**Não** acrescente esses 4 nomes ao `from app.patients import (...)` estático do Step 3 — os dois grupos usam estilos diferentes de propósito, e o Step 3 já traz o comentário que explica qual serve para quê.

- [ ] **Step 5: Confirmar que não sobrou import lazy de patients**

Run: `grep -n "from app.patients\|from app import patients" app/database.py`
Expected: exatamente **duas** ocorrências, ambas no topo do arquivo, sem
indentação e sem `# noqa: E402` — o `from app.patients import (...)` do Step 3 e
o `from app import patients` do Step 4. Nenhum import de patients pode sobrar
dentro de corpo de função.

- [ ] **Step 6: Rodar a suíte**

Run: `uv run pytest --tb=short`
Expected: PASS. `tests/test_database_shim.py` faz
`patch("app.database.get_contact_by_phone")` — segue funcionando, porque o nome
continua vinculado no namespace de `database.py`; só mudou de posição no arquivo.

- [ ] **Step 7: Commit**

```bash
git add app/database.py
git commit -m "refactor: sobe imports de app.patients para o topo de database.py

O import no fim do módulo era contorno do ciclo, que não existe mais.
Remove get_patients_by_contact do bloco: era re-export puro, sem uso interno."
```

---

### Task 5: Eliminar o re-export de `_phone_variants`

`app.database` não pode seguir exportando um helper de telefone que ele não usa — é a mesma armadilha de "dois lugares para importar a mesma coisa" que gerou este bug. Esta task reaponta os 28 call sites para `app.phone` e remove o re-export temporário criado na Task 1.

**Files:**
- Modify: `app/database.py` (o import da Task 1 perde a metade `_phone_variants`)
- Modify: `app/graph/tools.py:15`, `app/main.py` (240, 278, 721, 827, 914)
- Modify: `scripts/send_payment_reminders.py:163`, `scripts/release_pending_reschedules.py:109`
- Modify: 20 scripts one-off `_*.py` (lista completa no Step 3)
- Modify: `tests/test_import_graph.py` (adiciona asserção estrutural)

**Interfaces:**
- Consumes: `app.phone._phone_variants`, `app.phone._strip_phone` (Task 1).
- Produces: `app.database._phone_variants` **deixa de existir**. `app.database._strip_phone` continua existindo (uso interno, 3 chamadas).

- [ ] **Step 1: Adicionar a asserção que trava a regressão**

Acrescente ao fim de `tests/test_import_graph.py`:

```python
def test_ninguem_importa_phone_variants_de_database():
    """_phone_variants pertence a app.phone; app.database não o usa.

    Manter o re-export significaria dois caminhos de import para o mesmo
    símbolo — a confusão que produziu o ciclo patients/database.
    """
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if ".venv" in path.parts or path.name == "test_import_graph.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "from app.database import" in line and "_phone_variants" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "importe _phone_variants de app.phone:\n  " + "\n  ".join(offenders)
    )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_import_graph.py::test_ninguem_importa_phone_variants_de_database -v`
Expected: FAIL, listando 27 arquivos (os 28 menos `app/patients.py`, já corrigido na Task 3).

- [ ] **Step 3: Reapontar os 27 call sites**

Padrão mecânico em todos: separar o import misturado em duas linhas, preservando a indentação original (a maioria é import lazy dentro de função) e o `as _pv` onde houver.

`app/graph/tools.py:15` — remova `_phone_variants` da lista e adicione a linha seguinte:

```python
from app.database import get_supabase, log_event, upsert_user, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES
from app.phone import _phone_variants
```

`app/main.py` linhas 240, 278, 827, 914 — as quatro são idênticas:

```python
    from app.phone import _phone_variants as _pv
```

`app/main.py:721`:

```python
    from app.database import get_supabase
    from app.phone import _strip_phone
```

`scripts/send_payment_reminders.py:163`:

```python
    from app.phone import _phone_variants
```

`scripts/release_pending_reschedules.py:109`:

```python
        from app.database import get_supabase as _get_sb
        from app.phone import _strip_phone
```

Os 20 one-off, todos com indentação de 4 espaços (exceto onde indicado). Para cada um, retire o helper da lista de `app.database` e acrescente a linha de `app.phone`:

| Arquivo | Linha | Helper a mover |
|---|---|---|
| `scripts/_check_5581973260856.py` | 6 | `_phone_variants` |
| `scripts/_check_5581979087152.py` | 9 | `_phone_variants` |
| `scripts/_check_5581985806544.py` | 6 | `_phone_variants` |
| `scripts/_check_5581988054825.py` | 6 | `_phone_variants` |
| `scripts/_check_5581988851971.py` | 9 | `_phone_variants` |
| `scripts/_check_5581991947587_larissa.py` | 9 | `_phone_variants` |
| `scripts/_check_5581994358739_convo.py` | 6 | `_strip_phone` |
| `scripts/_check_5581994566910.py` | 8 | `_phone_variants` |
| `scripts/_check_5581995186399.py` | 6 | `_phone_variants` |
| `scripts/_check_5581996937559.py` | 8 | `_phone_variants` |
| `scripts/_check_5581997828165.py` | 6 | `_phone_variants` |
| `scripts/_check_5581998696027_events.py` | 6 | `_phone_variants` |
| `scripts/_check_5581998696027_full.py` | 6 | `_phone_variants` |
| `scripts/_check_5581998696027_msgs.py` | 6 | `_phone_variants` |
| `scripts/_check_5581999480798.py` | 6 | `_strip_phone` |
| `scripts/_check_5581999735649.py` | 6 | `_phone_variants` |
| `scripts/_check_5581999784308_patients.py` | 8 | `_phone_variants` |
| `scripts/_check_heitor_msgs2.py` | 6 | `_phone_variants` |
| `scripts/_check_patient_registro.py` | 6 | `_phone_variants` |
| `scripts/_identify_stuck_threads.py` | 21 | `_phone_variants` (indentação de **12** espaços) |

Exemplo, `scripts/_check_5581973260856.py:6`:

```python
    from app.database import get_supabase, get_users_by_phone
    from app.phone import _phone_variants
```

Quando o import de `app.database` ficar sem nenhum símbolo restante, remova a linha inteira em vez de deixar um import vazio.

- [ ] **Step 4: Remover o re-export de `app/database.py`**

O import da Task 1 perde a metade temporária e o `# noqa`:

```python
# _strip_phone: uso interno (3 chamadas, linhas 313/553/568).
from app.phone import _strip_phone
```

- [ ] **Step 5: Confirmar que o re-export morreu**

Run: `uv run pytest tests/test_import_graph.py -v`
Expected: PASS em todos os casos, incluindo o novo.

- [ ] **Step 6: Confirmar que os 20 one-off ainda importam**

Run: `uv run python -c "import pathlib,importlib; [importlib.import_module('scripts.'+p.stem) for p in pathlib.Path('scripts').glob('_check_*.py')]"`
Expected: exit 0. Estes scripts não têm cobertura de teste; este import é a única verificação de que as edições não os quebraram.

- [ ] **Step 7: Rodar a suíte**

Run: `uv run pytest --tb=short`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: importa _phone_variants de app.phone em todos os call sites

Remove o re-export de app.database, que não usava o símbolo. Deixa uma
única fonte canônica para cada helper de telefone."
```

---

### Task 6: Reapontar os call sites para `app.patients`

**Files:**
- Modify: `app/main.py:22`
- Modify: `scripts/_link_isabela_financeiro.py:14`
- Modify: `scripts/_link_fabricia_valdemar_roles.py:14`
- Modify: `scripts/_link_dione_pedro_lins.py:16`
- Modify: `scripts/_check_5581996571022.py:9`

**Interfaces:**
- Consumes: `app.patients.get_contact_by_phone`, `app.patients.link_patient_contact` (já existentes).
- Produces: nada. Limpeza — deixa uma única forma canônica de importar cada função.

- [ ] **Step 1: Corrigir `app/main.py`**

Substitua a linha 22:

```python
from app.database import get_user_by_phone, get_users_by_phone, get_contact_by_phone, log_event, DOCTOR_NAMES, save_message
```

por:

```python
from app.database import get_user_by_phone, get_users_by_phone, log_event, DOCTOR_NAMES, save_message
from app.patients import get_contact_by_phone
```

- [ ] **Step 2: Corrigir os 3 scripts `_link_*`**

Em `scripts/_link_isabela_financeiro.py:14`,
`scripts/_link_fabricia_valdemar_roles.py:14` e
`scripts/_link_dione_pedro_lins.py:16`, substitua:

```python
    from app.database import get_supabase, link_patient_contact
```

por:

```python
    from app.database import get_supabase
    from app.patients import link_patient_contact
```

- [ ] **Step 3: Corrigir `scripts/_check_5581996571022.py`**

Substitua a linha 9:

```python
    from app.database import get_users_by_phone, get_contact_by_phone, get_supabase
```

por:

```python
    from app.database import get_users_by_phone, get_supabase
    from app.patients import get_contact_by_phone
```

- [ ] **Step 4: Confirmar que não sobrou call site**

Run: `grep -rn --include='*.py' "from app.database import" . | grep -E "get_contact_by_phone|get_patients_by_contact|upsert_contact|upsert_patient|link_patient_contact"`
Expected: nenhuma saída.

- [ ] **Step 5: Verificar que os 4 scripts editados ainda importam**

Run: `uv run python -c "import scripts._link_isabela_financeiro, scripts._link_fabricia_valdemar_roles, scripts._link_dione_pedro_lins, scripts._check_5581996571022"`
Expected: exit 0, sem saída.

- [ ] **Step 6: Rodar a suíte**

Run: `uv run pytest --tb=short`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py scripts/_link_isabela_financeiro.py scripts/_link_fabricia_valdemar_roles.py scripts/_link_dione_pedro_lins.py scripts/_check_5581996571022.py
git commit -m "refactor: importa funções de patients direto de app.patients"
```

---

### Task 7: Remover os guards de ordem de import

Os `import app.database  # noqa: F401` existiam só para contornar o ciclo. Removê-los **é** a prova de que a causa raiz sumiu: se algum cron quebrar aqui, o ciclo não foi eliminado de verdade.

**Files:**
- Modify: `scripts/send_appointment_reminders.py`
- Modify: `scripts/complete_appointments.py`
- Modify: `scripts/send_no_show_messages.py`
- Modify: `scripts/send_return_reminders.py`

**Interfaces:**
- Consumes: grafo de imports acíclico (Task 3).
- Produces: nada. Remoção de dead code.

- [ ] **Step 1: Localizar os guards**

Run: `grep -rn "import app.database  # noqa: F401" scripts/`
Expected: 4 linhas, uma em cada script acima.

- [ ] **Step 2: Remover as 4 linhas**

Delete de cada um dos 4 scripts a linha:

```python
import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
```

**Não** adicione nada em `scripts/send_payment_reminders.py` — ele nunca teve o
guard, e o ponto é justamente que agora não precisa.

- [ ] **Step 3: Rodar o teste de regressão**

Run: `uv run pytest tests/test_import_graph.py -v`
Expected: PASS nos 10 casos, **agora sem nenhum guard no repositório**. É esta
execução que prova a correção: os 6 entrypoints importam em processo limpo por
mérito do grafo, não de remendo.

- [ ] **Step 4: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS.

- [ ] **Step 5: Verificação final do grafo**

Run: `grep -rn "app.database" app/patients.py app/phone.py app/supabase_client.py`
Expected: nenhuma saída — as folhas e `patients.py` estão limpos.

- [ ] **Step 6: Commit**

```bash
git add scripts/send_appointment_reminders.py scripts/complete_appointments.py scripts/send_no_show_messages.py scripts/send_return_reminders.py
git commit -m "refactor: remove os guards de ordem de import dos crons

Eram contorno do ciclo patients/database, eliminado na Task 3. Rodar
tests/test_import_graph.py sem nenhum guard no repo é a prova de que a
causa raiz sumiu."
```

---

## Verificação final (após a Task 6)

- [ ] `uv run pytest --tb=short` — suíte inteira verde.
- [ ] `uv run python scripts/send_payment_reminders.py` com env de produção **não** morre no import. (Fora do horário 7h-23h de Recife o script encerra sem fazer nada — isso é sucesso, não falha; o que importa é não haver `ImportError`.)
- [ ] Após o merge em `main`, conferir a primeira execução agendada:
      `gh run list --workflow=payment_reminders.yml --limit 3`.
      Esperado: `success` — o primeiro desde 2026-08-12T16:25:47Z.
