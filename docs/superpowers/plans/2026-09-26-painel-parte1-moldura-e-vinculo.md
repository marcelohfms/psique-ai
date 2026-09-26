# Painel da Eva, Parte 1: moldura nova e vínculo de contato — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O painel da atendente passa a ter o topo centrado no contato (nome em destaque, lápis para nome e CPF), a caixa de paciente com "+" para vincular, busca de paciente, criação de ficha, desvincular e a marcação "próprio paciente" coerente com a regra da idade.

**Architecture:** Tudo fica no app `dashboard/` (não importa `app/`). A camada de dados ganha funções puras e de banco em `dashboard/attendant_db.py`; as rotas novas entram em `dashboard/attendant_routes.py` com o mesmo token e o mesmo escopo por telefone; a tela `dashboard/templates/atendente.html` é refeita na moldura aprovada (paleta lavanda da Psiquê), mantendo o conteúdo atual das abas Financeiro e Cadastro. Um ajuste pequeno em `app/patients.py` faz a regra da idade reconhecer "tutor(a)".

**Tech Stack:** FastAPI, Jinja2, Supabase (postgrest-py async), Tailwind CDN + CSS próprio, JS puro; pytest + pytest-asyncio (asyncio_mode=auto).

**Spec:** `docs/superpowers/specs/2026-09-26-painel-vinculo-e-agendamento-design.md` (seções Interface e Parte 1). Protótipo: https://claude.ai/artifact/1PVtfTdPd6rggiR8MLVzH8

**Como rodar os testes:**
- Painel: `cd dashboard && uv run pytest -q` (hoje: 191 passando).
- Eva: `uv run pytest --tb=short` na raiz da worktree. Nunca passe `tests/test_patients.py` como primeiro argumento (import circular); use `uv run pytest tests/test_process_message.py tests/test_patients.py -q` se quiser rodar só ele.

---

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `app/patients.py` (modificar) | "tutor(a)" entra nas listas de responsável, para a regra da idade reconhecer a opção nova do painel. |
| `dashboard/tests/conftest.py` (modificar) | `FakeQuery.ilike` para a busca por nome. |
| `dashboard/attendant_db.py` (modificar) | `RELATIONSHIPS`, `normalize_marker`, `normalize_birth_date`, `search_patients`, `link_patient`, `find_patients_by_name_birth`, `create_patient`, `unlink_blocker`, `unlink_patient`; `resolve` passa a devolver o vínculo de cada paciente. |
| `dashboard/attendant_routes.py` (modificar) | Rotas `GET /pacientes/busca`, `POST /vinculo`, `POST /paciente-novo`, `POST /desvincular`; validação do marcador em `POST /vinculo/{pc_id}`. |
| `dashboard/main.py` (modificar) | Passa `RELATIONSHIPS` ao template. |
| `dashboard/templates/atendente.html` (reescrever) | Moldura nova + folha lateral de vínculo. |
| `dashboard/tests/test_attendant_db.py`, `test_attendant_routes.py`, `test_attendant_scope.py`, `test_main_auth.py` (modificar) | Testes novos. |
| `tests/test_patients.py` (modificar) | Teste de "tutor(a)". |

---

### Task 1: Regra da idade reconhece "tutor(a)"

**Files:**
- Modify: `app/patients.py:309-342` (`_GUARDIAN_RELATIONSHIPS`, `_LEGAL_GUARDIAN_RELATIONSHIPS`)
- Test: `tests/test_patients.py` (perto da linha 660, `test_is_guardian_relationship`)

- [ ] **Step 1: Write the failing test**

Adicione logo depois de `test_is_guardian_relationship` em `tests/test_patients.py`:

```python
def test_tutor_a_do_painel_conta_como_responsavel_legal():
    """O painel grava o parentesco como "tutor(a)" (lista fechada)."""
    from app.patients import _is_legal_guardian
    assert _is_guardian_relationship("tutor(a)") is True
    assert _is_legal_guardian("tutor(a)") is True
    assert _is_legal_guardian("Tutor(a)") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_process_message.py tests/test_patients.py -q -k tutor_a`
Expected: FAIL (`assert False is True`)

- [ ] **Step 3: Write minimal implementation**

Em `app/patients.py`, acrescente `"tutor(a)"` aos dois conjuntos:

```python
_GUARDIAN_RELATIONSHIPS = {
    "mãe", "mae", "pai", "tutor", "tutora", "tutor(a)", "responsável", "responsavel",
    "responsavel legal", "responsável legal", "avó", "avo", "avô",
    "tio", "tia", "irmã", "irma", "irmão", "irmao", "padrasto", "madrasta",
    "guardião", "guardiao",
}
```

```python
_LEGAL_GUARDIAN_RELATIONSHIPS = {
    "mãe", "mae", "pai", "tutor", "tutora", "tutor(a)", "responsável", "responsavel",
    "responsavel legal", "responsável legal", "avó", "avo", "avô",
    "guardião", "guardiao",
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_process_message.py tests/test_patients.py -q -k "tutor_a or guardian"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/patients.py tests/test_patients.py
git commit -m "fix(patients): regra da idade reconhece o parentesco tutor(a) do painel"
```

---

### Task 2: `FakeQuery.ilike` no banco falso do painel

**Files:**
- Modify: `dashboard/tests/conftest.py` (classe `FakeQuery`)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing test**

No fim de `dashboard/tests/test_attendant_db.py`:

```python
# ── Banco falso: ilike ────────────────────────────────────────────────────────


async def test_fake_ilike_casa_curinga_e_ignora_caixa(fake_client):
    fake_client.store["patients"] = [
        {"id": "p1", "name": "João Menezes"},
        {"id": "p2", "name": "Maria Souza"},
    ]
    res = await fake_client.from_("patients").select("id").ilike("name", "%j__o m%").execute()
    assert [r["id"] for r in res.data] == ["p1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k fake_ilike`
Expected: FAIL with `AttributeError: 'FakeQuery' object has no attribute 'ilike'`

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/tests/conftest.py`, adicione `import re` no topo e, dentro de `FakeQuery`, o método (depois de `neq`):

```python
    def ilike(self, col, pattern):
        self._filters.append(("ilike", col, pattern))
        return self
```

E em `_matches`, antes do `return True`:

```python
            if kind == "ilike":
                rx = "".join(
                    ".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in val
                )
                if not re.fullmatch(rx, row.get(col) or "", re.IGNORECASE | re.DOTALL):
                    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k fake_ilike`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/tests/conftest.py dashboard/tests/test_attendant_db.py
git commit -m "test(painel): banco falso suporta ilike"
```

---

### Task 3: Marcador "próprio paciente" e lista fechada de parentesco

**Files:**
- Modify: `dashboard/attendant_db.py` (nova seção depois de `_filter`)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Marcador do vínculo ───────────────────────────────────────────────────────


def test_marker_proprio_zera_parentesco():
    assert attendant_db.normalize_marker(True, "mãe") == {"is_self": True, "relationship": None}


def test_marker_terceiro_exige_parentesco_da_lista():
    assert attendant_db.normalize_marker(False, "  mãe ") == {"is_self": False, "relationship": "mãe"}
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(False, "")
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(False, "vizinha")
    with pytest.raises(ValueError):
        attendant_db.normalize_marker(None, None)


def test_lista_de_parentesco_tem_acompanhante_e_nao_tem_outro():
    assert "acompanhante" in attendant_db.RELATIONSHIPS
    assert "outro" not in attendant_db.RELATIONSHIPS
    assert attendant_db.RELATIONSHIPS[0] == "mãe"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k "marker or parentesco"`
Expected: FAIL with `AttributeError: module 'attendant_db' has no attribute 'normalize_marker'`

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_db.py`, logo depois de `_filter`:

```python
# ── Marcador do vínculo (próprio paciente x parentesco) ──────────────────────

# Lista fechada: é o que a regra da idade (app/patients.py) sabe interpretar.
# "tutor(a)" e "responsável legal" contam como responsável legal; "cônjuge" e
# "acompanhante" contam como terceiros que não são responsáveis.
RELATIONSHIPS = (
    "mãe", "pai", "avó", "avô", "tutor(a)", "responsável legal", "tio", "tia",
    "irmão", "irmã", "padrasto", "madrasta", "cônjuge", "acompanhante",
)


def normalize_marker(is_self, relationship) -> dict:
    """Valida e normaliza o marcador de um par (paciente, contato).

    A regra da idade só trata o número como "próprio" quando is_self é True E o
    parentesco está vazio. Por isso "próprio" sempre grava relationship=None, e
    terceiro exige um parentesco da lista fechada. ValueError quando inválido.
    """
    if is_self:
        return {"is_self": True, "relationship": None}
    rel = (relationship or "").strip()
    if rel not in RELATIONSHIPS:
        raise ValueError("Escolha o parentesco da lista.")
    return {"is_self": False, "relationship": rel}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k "marker or parentesco"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): marcador próprio paciente e lista fechada de parentesco"
```

---

### Task 4: `resolve` devolve o vínculo de cada paciente

A caixa de paciente mostra "Lucas Menezes · número da mãe", então cada paciente precisa vir com o marcador do par.

**Files:**
- Modify: `dashboard/attendant_db.py` (`_get_patients_by_contact`)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_resolve_traz_vinculo_de_cada_paciente_preferindo_agendamento(patched_client):
    patched_client.store["contacts"] = [{"id": "c1", "phone": "5581999998888"}]
    patched_client.store["patient_contacts"] = [
        {"id": "pc-fin", "contact_id": "c1", "patient_id": "p1", "role": "financeiro",
         "is_self": False, "relationship": "pai", "patients": {"id": "p1", "name": "João"}},
        {"id": "pc-ag", "contact_id": "c1", "patient_id": "p1", "role": "agendamento",
         "is_self": False, "relationship": "mãe", "patients": {"id": "p1", "name": "João"}},
        {"id": "pc-2", "contact_id": "c1", "patient_id": "p2", "role": "agendamento",
         "is_self": True, "relationship": None, "patients": {"id": "p2", "name": "Carla"}},
    ]
    out = await attendant_db.resolve_contact_and_patients("5581999998888")
    by_id = {p["id"]: p for p in out["patients"]}
    assert by_id["p1"]["link"] == {"id": "pc-ag", "is_self": False, "relationship": "mãe"}
    assert by_id["p2"]["link"] == {"id": "pc-2", "is_self": True, "relationship": None}
    assert [p["id"] for p in out["patients"]] == ["p1", "p2"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k traz_vinculo`
Expected: FAIL with `KeyError: 'link'`

- [ ] **Step 3: Write minimal implementation**

Substitua `_get_patients_by_contact` inteira:

```python
async def _get_patients_by_contact(client, contact_id: str) -> list[dict]:
    """Pacientes ligados ao contato, sem repetição, cada um com `link`: o
    marcador do par (id da linha, is_self, relationship). Prefere a linha
    `agendamento`, a mesma que get_link devolve ao painel."""
    res = (
        await client.from_("patient_contacts")
        .select("id, patient_id, role, is_self, relationship, patients(*)")
        .eq("contact_id", contact_id)
        .execute()
    )
    by_id: dict[str, dict] = {}
    for row in (res.data or []):
        patient = row.get("patients")
        if not patient:
            continue
        marker = {
            "id": row.get("id"),
            "is_self": bool(row.get("is_self")),
            "relationship": row.get("relationship"),
        }
        entry = by_id.get(patient["id"])
        if entry is None:
            by_id[patient["id"]] = {**patient, "link": marker}
        elif row.get("role") == "agendamento":
            entry["link"] = marker
    return list(by_id.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS (incluindo `test_resolve_finds_contact_and_patients`, que continua valendo)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): resolve devolve o vínculo de cada paciente do número"
```

---

### Task 5: Busca de paciente por nome, sem acento e sem caixa

**Files:**
- Modify: `dashboard/attendant_db.py` (nova seção "Busca de paciente")
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Busca de paciente ─────────────────────────────────────────────────────────


async def test_busca_ignora_acento_e_caixa(patched_client):
    patched_client.store["patients"] = [
        {"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014"},
        {"id": "p2", "name": "Joana Lima", "birth_date": "01/01/1990"},
        {"id": "p3", "name": "Pedro Alves", "birth_date": "02/02/1980"},
    ]
    patched_client.store["patient_contacts"] = [
        {"patient_id": "p1", "is_self": True, "contacts": {"phone": "5581999995432"}},
        {"patient_id": "p1", "is_self": False, "contacts": {"phone": "5581911110000"}},
    ]
    out = await attendant_db.search_patients("JOAO men")
    assert out == [{"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014",
                    "phone_hint": "5432"}]


async def test_busca_com_menos_de_3_letras_nao_consulta(patched_client):
    patched_client.store["patients"] = [{"id": "p1", "name": "Jo"}]
    assert await attendant_db.search_patients("jo") == []


async def test_busca_ordena_por_nome_e_limita(patched_client):
    patched_client.store["patients"] = [
        {"id": f"p{i}", "name": f"Ana {chr(90 - i)}", "birth_date": None} for i in range(15)
    ]
    out = await attendant_db.search_patients("ana", limit=3)
    assert [p["name"] for p in out] == ["Ana L", "Ana M", "Ana N"]
    assert all(p["phone_hint"] is None for p in out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k busca`
Expected: FAIL with `AttributeError: ... 'search_patients'`

- [ ] **Step 3: Write minimal implementation**

No topo de `dashboard/attendant_db.py`, junto dos imports: `import unicodedata`. Depois da seção "Leitura de paciente + vínculo":

```python
# ── Busca de paciente (vincular) ──────────────────────────────────────────────

SEARCH_MIN_CHARS = 3
_ACCENTABLE = set("aeiouc")


def _norm(text: str | None) -> str:
    """Sem acento, minúsculo, espaços colapsados. Espelha normalize_person_name."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)
    )
    return " ".join(stripped.lower().split())


def _ilike_pattern(query: str) -> str:
    """Padrão ILIKE que tolera acento: toda letra que pode ter acento vira `_`
    (um caractere qualquer). O filtro exato sem acento é feito depois, em Python."""
    folded = _norm(query)
    return "%" + "".join("_" if ch in _ACCENTABLE else ch for ch in folded) + "%"


async def search_patients(query: str, limit: int = 10) -> list[dict]:
    """Pacientes cujo nome contém `query` (sem acento e sem caixa).

    Devolve só o necessário para diferenciar homônimos: id, nome, nascimento e
    os 4 últimos dígitos do número próprio do paciente (phone_hint), se houver.
    """
    target = _norm(query)
    if len(target) < SEARCH_MIN_CHARS:
        return []
    client = await get_client()
    res = await (
        client.from_("patients")
        .select("id, name, birth_date")
        .ilike("name", _ilike_pattern(query))
        .limit(200)
        .execute()
    )
    hits = [r for r in (res.data or []) if target in _norm(r.get("name"))]
    hits.sort(key=lambda r: _norm(r.get("name")))
    hits = hits[:limit]
    if not hits:
        return []

    pcs = await (
        client.from_("patient_contacts")
        .select("patient_id, is_self, contacts(phone)")
        .in_("patient_id", [h["id"] for h in hits])
        .eq("is_self", True)
        .execute()
    )
    hint_by_patient: dict[str, str] = {}
    for row in (pcs.data or []):
        phone = (row.get("contacts") or {}).get("phone") or ""
        if phone and row["patient_id"] not in hint_by_patient:
            hint_by_patient[row["patient_id"]] = phone[-4:]

    return [
        {"id": h["id"], "name": h.get("name"), "birth_date": h.get("birth_date"),
         "phone_hint": hint_by_patient.get(h["id"])}
        for h in hits
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k busca`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): busca de paciente por nome sem acento"
```

---

### Task 6: Vincular cria os três papéis com o mesmo marcador

**Files:**
- Modify: `dashboard/attendant_db.py` (nova seção "Vincular e desvincular")
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Vincular ──────────────────────────────────────────────────────────────────


async def test_vincular_cria_os_tres_papeis(patched_client):
    await attendant_db.link_patient("p1", "c1", {"is_self": False, "relationship": "mãe"})
    rows = patched_client.store["patient_contacts"]
    assert sorted(r["role"] for r in rows) == ["agendamento", "consulta", "financeiro"]
    assert all(r["patient_id"] == "p1" and r["contact_id"] == "c1" for r in rows)
    assert all(r["is_self"] is False and r["relationship"] == "mãe" for r in rows)


async def test_vincular_completa_papeis_faltantes_e_alinha_marcador(patched_client):
    patched_client.store["patient_contacts"] = [
        {"id": "pc1", "patient_id": "p1", "contact_id": "c1", "role": "agendamento",
         "is_self": True, "relationship": "mãe"},
    ]
    await attendant_db.link_patient("p1", "c1", {"is_self": True, "relationship": None})
    rows = patched_client.store["patient_contacts"]
    assert len(rows) == 3
    assert all(r["is_self"] is True and r["relationship"] is None for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k vincular`
Expected: FAIL with `AttributeError: ... 'link_patient'`

- [ ] **Step 3: Write minimal implementation**

Depois da seção de busca:

```python
# ── Vincular e desvincular ────────────────────────────────────────────────────

LINK_ROLES = ("agendamento", "financeiro", "consulta")


async def link_patient(patient_id: str, contact_id: str, marker: dict) -> None:
    """Liga o contato ao paciente nos três papéis, com o mesmo marcador.

    Idempotente: cria só os papéis que faltam e depois alinha o marcador em
    todas as linhas do par. A regra da idade decide na hora do envio quem recebe
    o quê; aqui o que importa é o marcador estar certo e igual nas três linhas.
    """
    client = await get_client()
    res = await (
        client.from_("patient_contacts")
        .select("role")
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
    have = {r.get("role") for r in (res.data or [])}
    for role in LINK_ROLES:
        if role not in have:
            await client.from_("patient_contacts").insert({
                "patient_id": patient_id, "contact_id": contact_id, "role": role, **marker,
            }).execute()
    await (
        client.from_("patient_contacts").update(marker)
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k vincular`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): vincular contato ao paciente nos três papéis"
```

---

### Task 7: Criar ficha nova com checagem de duplicada

**Files:**
- Modify: `dashboard/attendant_db.py` (seção "Vincular e desvincular"; `import uuid` no topo)
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Ficha nova ────────────────────────────────────────────────────────────────


def test_nascimento_aceita_br_e_iso_e_devolve_br():
    assert attendant_db.normalize_birth_date("06/05/2014") == "06/05/2014"
    assert attendant_db.normalize_birth_date(" 2014-05-06 ") == "06/05/2014"
    for ruim in ("", "31/02/2014", "amanhã", "01/01/2999"):
        with pytest.raises(ValueError):
            attendant_db.normalize_birth_date(ruim)


async def test_duplicada_por_nome_e_nascimento_nas_duas_grafias(patched_client):
    patched_client.store["patients"] = [
        {"id": "p1", "name": "João  Menezes", "birth_date": "2014-05-06"},
        {"id": "p2", "name": "João Menezes", "birth_date": "07/05/2014"},
    ]
    out = await attendant_db.find_patients_by_name_birth("joao menezes", "06/05/2014")
    assert [p["id"] for p in out] == ["p1"]


async def test_criar_ficha_grava_nome_limpo_e_devolve_id(patched_client):
    created = await attendant_db.create_patient("  Ana   Luz ", "01/02/2015")
    row = patched_client.store["patients"][0]
    assert row["name"] == "Ana Luz" and row["birth_date"] == "01/02/2015"
    assert created == {"id": row["id"], "name": "Ana Luz", "birth_date": "01/02/2015"}
    assert len(created["id"]) == 36
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k "nascimento or duplicada or criar_ficha"`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

No topo: `import uuid` e `from datetime import date, datetime` (troque o import atual de `datetime`). Depois de `link_patient`:

```python
def normalize_birth_date(raw: str) -> str:
    """Aceita dd/mm/aaaa ou aaaa-mm-dd e devolve dd/mm/aaaa, o formato que o
    fluxo do chat grava em patients.birth_date. ValueError se inválida ou futura."""
    text = (raw or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if d > date.today():
            raise ValueError("Data de nascimento no futuro.")
        return d.strftime("%d/%m/%Y")
    raise ValueError("Data de nascimento inválida. Use dd/mm/aaaa.")


def _birth_variants(birth_br: str) -> list[str]:
    d = datetime.strptime(birth_br, "%d/%m/%Y")
    return [d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d")]


async def find_patients_by_name_birth(name: str, birth_br: str) -> list[dict]:
    """Fichas com o mesmo nome (sem acento/caixa/espaços) e o mesmo nascimento,
    nas duas grafias que convivem no banco. Espelha find_patient_by_name_birth."""
    client = await get_client()
    res = await (
        client.from_("patients")
        .select("id, name, birth_date")
        .in_("birth_date", _birth_variants(birth_br))
        .execute()
    )
    target = _norm(name)
    return [r for r in (res.data or []) if _norm(r.get("name")) == target]


async def create_patient(name: str, birth_br: str) -> dict:
    """Cria a ficha com nome e nascimento. O id é gerado aqui para a resposta
    não depender do retorno do insert."""
    row = {"id": str(uuid.uuid4()), "name": " ".join(name.split()), "birth_date": birth_br}
    client = await get_client()
    await client.from_("patients").insert(row).execute()
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): criar ficha nova com checagem de duplicada"
```

---

### Task 8: Desvincular com trava do último número

**Files:**
- Modify: `dashboard/attendant_db.py` (seção "Vincular e desvincular")
- Test: `dashboard/tests/test_attendant_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# ── Desvincular ───────────────────────────────────────────────────────────────


def _pc(pid, cid, role="agendamento"):
    return {"patient_id": pid, "contact_id": cid, "role": role, "is_self": False, "relationship": "mãe"}


async def test_trava_ultimo_numero_com_consulta_futura(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c1", "consulta")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    msg = await attendant_db.unlink_blocker("p1", "c1")
    assert msg is not None and "02/10" in msg and "14:00" in msg


async def test_sem_trava_quando_ha_outro_numero(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1"), _pc("p1", "c2")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2099-10-02T14:00:00-03:00"},
    ]
    assert await attendant_db.unlink_blocker("p1", "c1") is None


async def test_sem_trava_quando_so_ha_consulta_passada_ou_cancelada(patched_client):
    patched_client.store["patient_contacts"] = [_pc("p1", "c1")]
    patched_client.store["appointments"] = [
        {"patient_id": "p1", "status": "scheduled", "start_time": "2020-01-01T10:00:00-03:00"},
        {"patient_id": "p1", "status": "canceled", "start_time": "2099-01-01T10:00:00-03:00"},
    ]
    assert await attendant_db.unlink_blocker("p1", "c1") is None


async def test_desvincular_apaga_so_as_linhas_do_par(patched_client):
    patched_client.store["patient_contacts"] = [
        _pc("p1", "c1"), _pc("p1", "c1", "financeiro"), _pc("p1", "c2"), _pc("p2", "c1"),
    ]
    removed = await attendant_db.unlink_patient("p1", "c1")
    assert removed == 2
    assert sorted((r["patient_id"], r["contact_id"]) for r in patched_client.store["patient_contacts"]) == [
        ("p1", "c2"), ("p2", "c1"),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q -k "trava or desvincular"`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Write minimal implementation**

Depois de `create_patient`:

```python
_ACTIVE_APPT_STATUSES = ("scheduled", "pending_reschedule")


async def unlink_blocker(patient_id: str, contact_id: str) -> str | None:
    """Motivo para NÃO desvincular, ou None se pode.

    Trava só o caso perigoso: é o único número do paciente e ele tem consulta
    futura ativa (ficaria sem ninguém para receber lembrete e cobrança).
    """
    client = await get_client()
    others = await (
        client.from_("patient_contacts")
        .select("contact_id")
        .eq("patient_id", patient_id)
        .neq("contact_id", contact_id)
        .execute()
    )
    if others.data:
        return None
    appts = await (
        client.from_("appointments")
        .select("start_time")
        .eq("patient_id", patient_id)
        .in_("status", list(_ACTIVE_APPT_STATUSES))
        .gt("start_time", datetime.now(_TZ).isoformat())
        .order("start_time")
        .limit(1)
        .execute()
    )
    if not appts.data:
        return None
    when = datetime.fromisoformat(appts.data[0]["start_time"]).astimezone(_TZ)
    return (
        f"Este é o único número do paciente e ele tem consulta em "
        f"{when.strftime('%d/%m às %H:%M')}. Vincule outro número antes."
    )


async def unlink_patient(patient_id: str, contact_id: str) -> int:
    """Apaga todas as linhas do par (os três papéis). Retorna quantas saíram."""
    client = await get_client()
    res = await (
        client.from_("patient_contacts").delete()
        .eq("patient_id", patient_id)
        .eq("contact_id", contact_id)
        .execute()
    )
    return len(res.data or [])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest tests/test_attendant_db.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py dashboard/tests/test_attendant_db.py
git commit -m "feat(painel): desvincular com trava do último número com consulta futura"
```

---

### Task 9: Rotas de busca, vincular, ficha nova e desvincular

**Files:**
- Modify: `dashboard/attendant_routes.py`
- Test: `dashboard/tests/test_attendant_routes.py`, `dashboard/tests/test_attendant_scope.py`

- [ ] **Step 1: Write the failing tests (comportamento)**

No fim de `dashboard/tests/test_attendant_routes.py`:

```python
# ── Vínculo: busca, vincular, ficha nova, desvincular ────────────────────────

T = {"token": "test-token"}


def _scope_c1(monkeypatch, patient_ids=("p1",)):
    async def fake_scope(phone):
        return "c1", set(patient_ids)
    monkeypatch.setattr(attendant_db, "scope_for_phone", fake_scope)


def _events(monkeypatch):
    got = []
    async def fake_log(event_type, phone, metadata=None):
        got.append((event_type, metadata))
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    return got


def test_busca_devolve_resultados(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_search(q):
        assert q == "joao"
        return [{"id": "p9", "name": "João", "birth_date": None, "phone_hint": None}]
    monkeypatch.setattr(attendant_db, "search_patients", fake_search)
    r = client.get("/api/atendente/pacientes/busca", params={**T, "q": "joao", "phone": "5581"})
    assert r.status_code == 200 and r.json()[0]["id"] == "p9"


def test_vincular_usa_o_contato_do_telefone_e_normaliza(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    calls = []
    async def fake_get_patient(pid):
        return {"id": pid}
    async def fake_link(pid, cid, marker):
        calls.append((pid, cid, marker))
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    monkeypatch.setattr(attendant_db, "link_patient", fake_link)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "p9", "is_self": True, "relationship": "mãe", "agent": "Ana"})
    assert r.status_code == 200
    assert calls == [("p9", "c1", {"is_self": True, "relationship": None})]
    assert ev[0][0] == "attendant_link_patient" and ev[0][1]["agent"] == "Ana"


def test_vincular_recusa_parentesco_fora_da_lista(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "p9", "is_self": False, "relationship": "vizinha"})
    assert r.status_code == 400


def test_vincular_paciente_inexistente_404(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_get_patient(pid):
        return None
    monkeypatch.setattr(attendant_db, "get_patient", fake_get_patient)
    r = client.post("/api/atendente/vinculo", params=T, json={
        "phone": "5581", "patient_id": "nope", "is_self": True})
    assert r.status_code == 404


def test_ficha_nova_duplicada_devolve_409_com_a_ficha(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_find(name, birth):
        return [{"id": "p1", "name": "João Menezes", "birth_date": "06/05/2014"}]
    monkeypatch.setattr(attendant_db, "find_patients_by_name_birth", fake_find)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "joao menezes", "birth_date": "2014-05-06"})
    assert r.status_code == 409
    assert r.json()["detail"]["duplicates"][0]["id"] == "p1"


def test_ficha_nova_cria(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    async def fake_find(name, birth):
        return []
    async def fake_create(name, birth):
        assert (name, birth) == ("Ana Luz", "01/02/2015")
        return {"id": "novo", "name": name, "birth_date": birth}
    monkeypatch.setattr(attendant_db, "find_patients_by_name_birth", fake_find)
    monkeypatch.setattr(attendant_db, "create_patient", fake_create)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": " Ana  Luz ", "birth_date": "01/02/2015"})
    assert r.status_code == 200 and r.json()["patient"]["id"] == "novo"
    assert ev[0][0] == "attendant_create_patient"


def test_ficha_nova_nascimento_invalido_400(client, monkeypatch):
    _scope_c1(monkeypatch)
    r = client.post("/api/atendente/paciente-novo", params=T, json={
        "phone": "5581", "name": "Ana Luz", "birth_date": "31/02/2015"})
    assert r.status_code == 400


def test_desvincular_travado_409(client, monkeypatch):
    _scope_c1(monkeypatch)
    async def fake_blocker(pid, cid):
        return "Este é o único número do paciente..."
    monkeypatch.setattr(attendant_db, "unlink_blocker", fake_blocker)
    r = client.post("/api/atendente/desvincular", params=T, json={"phone": "5581", "patient_id": "p1"})
    assert r.status_code == 409 and "único número" in r.json()["detail"]


def test_desvincular_ok(client, monkeypatch):
    _scope_c1(monkeypatch)
    ev = _events(monkeypatch)
    async def fake_blocker(pid, cid):
        return None
    async def fake_unlink(pid, cid):
        assert (pid, cid) == ("p1", "c1")
        return 3
    monkeypatch.setattr(attendant_db, "unlink_blocker", fake_blocker)
    monkeypatch.setattr(attendant_db, "unlink_patient", fake_unlink)
    r = client.post("/api/atendente/desvincular", params=T, json={"phone": "5581", "patient_id": "p1"})
    assert r.status_code == 200 and r.json()["removed"] == 3
    assert ev[0][0] == "attendant_unlink_patient"


def test_editar_vinculo_normaliza_marcador(client, monkeypatch):
    got = {}
    async def fake_update(pc_id, data):
        got.update(data)
    async def fake_log(*a, **k):
        return None
    monkeypatch.setattr(attendant_db, "update_link", fake_update)
    monkeypatch.setattr(attendant_db, "log_event", fake_log)
    r = client.post("/api/atendente/vinculo/pc1", params=T,
                    json={"phone": "5581", "data": {"is_self": True, "relationship": "mãe"}})
    assert r.status_code == 200 and got == {"is_self": True, "relationship": None}
    r = client.post("/api/atendente/vinculo/pc1", params=T,
                    json={"phone": "5581", "data": {"is_self": False, "relationship": "vizinha"}})
    assert r.status_code == 400
```

No fim de `dashboard/tests/test_attendant_scope.py`:

```python
# ── vínculo ─────────────────────────────────────────────────────────────────

def test_desvincular_paciente_de_outro_numero_recusa(client, monkeypatch):
    _scope(monkeypatch, "c1", {"p1"})
    r = client.post("/api/atendente/desvincular", params=TOKEN, json={"phone": PHONE, "patient_id": "p2"})
    assert r.status_code == 403


def test_vincular_sem_contato_no_numero_recusa(client, monkeypatch):
    _scope(monkeypatch, None, set())
    r = client.post("/api/atendente/vinculo", params=TOKEN,
                    json={"phone": PHONE, "patient_id": "p1", "is_self": True})
    assert r.status_code == 403


def test_busca_sem_contato_no_numero_recusa(client, monkeypatch):
    _scope(monkeypatch, None, set())
    r = client.get("/api/atendente/pacientes/busca", params={**TOKEN, "q": "joao", "phone": PHONE})
    assert r.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && uv run pytest tests/test_attendant_routes.py tests/test_attendant_scope.py -q`
Expected: FAIL (rotas novas devolvem 404/405; `test_editar_vinculo_normaliza_marcador` falha no `got`)

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/attendant_routes.py`, junto dos outros modelos:

```python
class LinkBody(BaseModel):
    phone: str
    patient_id: str
    is_self: bool
    relationship: str | None = None
    agent: str = ""


class NewPatientBody(BaseModel):
    phone: str
    name: str
    birth_date: str
    agent: str = ""


class UnlinkBody(BaseModel):
    phone: str
    patient_id: str
    agent: str = ""


async def _contact_id_for(phone: str) -> str:
    """Contato da conversa. O vínculo sempre usa este, nunca um id vindo do cliente."""
    contact_id, _ = await attendant_db.scope_for_phone(phone)
    if contact_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)
    return contact_id
```

Substitua `update_vinculo`:

```python
@router.post("/vinculo/{pc_id}")
async def update_vinculo(pc_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_link_scope(body.phone, pc_id)
    data = dict(body.data)
    if "is_self" in data or "relationship" in data:
        try:
            data.update(attendant_db.normalize_marker(data.get("is_self"), data.get("relationship")))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    await attendant_db.update_link(pc_id, data)
    await attendant_db.log_event("attendant_edit_link", body.phone,
                                 {"pc_id": pc_id, "fields": list(data.keys())})
    return {"ok": True}
```

E acrescente, antes da seção de pagamentos:

```python
# ── Vínculo: busca, vincular, ficha nova, desvincular ────────────────────────


@router.get("/pacientes/busca")
async def buscar_pacientes(q: str, phone: str, _: None = Depends(verify_token)):
    # A busca olha a base toda (é para achar quem ainda não está ligado), então
    # devolve só nome, nascimento e 4 dígitos. Exige que o número tenha contato.
    await _contact_id_for(phone)
    return await attendant_db.search_patients(q)


@router.post("/vinculo")
async def criar_vinculo(body: LinkBody, _: None = Depends(verify_token)):
    contact_id = await _contact_id_for(body.phone)
    try:
        marker = attendant_db.normalize_marker(body.is_self, body.relationship)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if await attendant_db.get_patient(body.patient_id) is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    await attendant_db.link_patient(body.patient_id, contact_id, marker)
    await attendant_db.log_event("attendant_link_patient", body.phone, {
        "patient_id": body.patient_id, "contact_id": contact_id, **marker, "agent": body.agent})
    return {"ok": True}


@router.post("/paciente-novo")
async def criar_paciente(body: NewPatientBody, _: None = Depends(verify_token)):
    await _contact_id_for(body.phone)
    name = " ".join(body.name.split())
    if len(name) < 3:
        raise HTTPException(status_code=400, detail="Informe o nome completo.")
    try:
        birth = attendant_db.normalize_birth_date(body.birth_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dups = await attendant_db.find_patients_by_name_birth(name, birth)
    if dups:
        raise HTTPException(status_code=409, detail={
            "message": "Já existe ficha com este nome e nascimento.",
            "duplicates": [{"id": d["id"], "name": d.get("name"), "birth_date": d.get("birth_date")}
                           for d in dups],
        })
    patient = await attendant_db.create_patient(name, birth)
    await attendant_db.log_event("attendant_create_patient", body.phone,
                                 {"patient_id": patient["id"], "agent": body.agent})
    return {"ok": True, "patient": patient}


@router.post("/desvincular")
async def desvincular(body: UnlinkBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    contact_id = await _contact_id_for(body.phone)
    blocker = await attendant_db.unlink_blocker(body.patient_id, contact_id)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    removed = await attendant_db.unlink_patient(body.patient_id, contact_id)
    await attendant_db.log_event("attendant_unlink_patient", body.phone, {
        "patient_id": body.patient_id, "contact_id": contact_id, "removed": removed,
        "agent": body.agent})
    return {"ok": True, "removed": removed}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS (todos, incluindo os 191 anteriores)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_routes.py dashboard/tests/test_attendant_routes.py dashboard/tests/test_attendant_scope.py
git commit -m "feat(painel): rotas de busca, vincular, ficha nova e desvincular"
```

---

### Task 10: Template recebe a lista de parentesco

**Files:**
- Modify: `dashboard/main.py` (`atendente_page`, imports)
- Test: `dashboard/tests/test_main_auth.py`

- [ ] **Step 1: Write the failing test**

No fim de `dashboard/tests/test_main_auth.py`:

```python
def test_atendente_renderiza_moldura_e_lista_de_parentesco():
    r = _client().get("/atendente", params={"token": PANEL_TOKEN})
    assert r.status_code == 200
    for marca in ('id="contact-name"', 'id="patient-box"', 'id="link-sheet"',
                  'data-tab="financeiro"', 'data-tab="cadastro"', 'data-tab="reset"',
                  '<option value="acompanhante">acompanhante</option>',
                  '<option value="tutor(a)">tutor(a)</option>'):
        assert marca in r.text, marca
    assert 'data-tab="contato"' not in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && uv run pytest tests/test_main_auth.py -q -k moldura`
Expected: FAIL (`id="contact-name"` ausente)

- [ ] **Step 3: Write minimal implementation**

Em `dashboard/main.py`, junto de `import attendant_routes`: `import attendant_db`. Em `atendente_page`:

```python
    return templates.TemplateResponse(request, "atendente.html", {
        "token": ATTENDANT_PANEL_TOKEN,
        "relationships": attendant_db.RELATIONSHIPS,
    })
```

(O teste só passa depois da Task 11; siga para ela sem commitar.)

---

### Task 11: Tela nova (moldura + folha de vínculo)

**Files:**
- Rewrite: `dashboard/templates/atendente.html`

O arquivo novo tem quatro blocos: estilo, marcação, script novo e funções mantidas. As funções de pagamentos e da ficha continuam iguais às de hoje; elas são copiadas do arquivo atual sem mudança.

- [ ] **Step 1: Copie o arquivo atual para consulta**

Run: `cp dashboard/templates/atendente.html /tmp/atendente.old.html`

- [ ] **Step 2: Escreva o início do arquivo (cabeçalho, estilo e marcação)**

Substitua todo o conteúdo de `dashboard/templates/atendente.html`, do começo até a linha `<script>` inclusive, por:

```html
{% extends "base.html" %}
{# Configuração no Chatwoot: Settings → Integrations → Dashboard Apps → New.
   Endpoint URL: https://<dashboard>/atendente?token=<ATTENDANT_PANEL_TOKEN>
   O Chatwoot envia o contato da conversa via postMessage (appContext). #}
{% block content %}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap" rel="stylesheet">
<style>
  body { overflow: auto !important; height: auto !important; }
  .eva {
    --bg: #FAF6F0; --surface: #FFFFFF; --ink: #42424A; --muted: #6E6D77; --line: #EADFD0;
    --soft: #F3ECE2; --accent: #575684; --accent-dk: #3F3E63; --accent-soft: #EDECF4;
    --danger: #B42335; --ok: #2F8F6B; --on-accent: #FFFFFF;
    font-family: 'Geist', system-ui, sans-serif; color: var(--ink); background: var(--bg);
    -webkit-font-smoothing: antialiased;
  }
  @media (prefers-color-scheme: dark) {
    .eva { --bg: #17161D; --surface: #211F29; --ink: #ECEAF2; --muted: #A5A3B3; --line: #34313F;
           --soft: #2A2834; --accent: #A9A8D6; --accent-dk: #C4C3E6; --accent-soft: #2E2C40;
           --danger: #F07A8A; --ok: #5BC49A; --on-accent: #17161D; }
  }
  .eva .serif { font-family: 'Newsreader', Georgia, serif; font-weight: 400; }
  .eva .num { font-variant-numeric: tabular-nums; }
  .eva .cap { font-size: 11px; font-weight: 500; letter-spacing: .09em; text-transform: uppercase; color: var(--muted); }
  .eva .card { background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
  .eva .inp { height: 42px; box-sizing: border-box; padding: 0 12px; border-radius: 10px; border: 1px solid var(--line);
              background: var(--surface); color: var(--ink); font: inherit; font-size: 14px; width: 100%; }
  .eva .btn { height: 38px; padding: 0 16px; border-radius: 10px; font-size: 13px; font-weight: 500;
              border: 1px solid var(--line); background: var(--surface); color: var(--ink); cursor: pointer;
              transition: background-color .18s ease, border-color .18s ease; }
  .eva .btn-primary { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .eva .btn-primary:hover { background: var(--accent-dk); border-color: var(--accent-dk); }
  .eva .btn:disabled { opacity: .45; cursor: default; }
  .eva .ibtn { width: 34px; height: 34px; border-radius: 9px; border: 0; background: transparent; color: var(--muted);
               display: inline-flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; }
  .eva .ibtn:hover { background: var(--soft); color: var(--ink); }
  .eva button:focus-visible, .eva input:focus-visible, .eva select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .eva .av { width: 36px; height: 36px; border-radius: 10px; background: var(--accent-soft); color: var(--accent);
             font-size: 12px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
  .eva .pbox { width: 420px; max-width: 100%; height: 52px; padding: 0 14px 0 8px; border-radius: 12px; border: 1px solid var(--line);
               background: var(--surface); display: flex; align-items: center; gap: 12px; text-align: left; cursor: pointer; color: var(--ink); }
  .eva .pbox[aria-expanded="true"] { border-color: var(--accent); }
  .eva .plus { width: 52px; height: 52px; border-radius: 12px; border: 1px dashed var(--accent); background: transparent;
               color: var(--accent); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
  .eva .dropdown { position: absolute; top: 58px; left: 0; width: 420px; max-width: 100%; padding: 6px; z-index: 20;
                   box-shadow: 0 16px 36px rgba(66,66,74,.14); }
  .eva .opt { display: flex; align-items: center; gap: 4px; border-radius: 10px; }
  .eva .opt[aria-selected="true"] { background: var(--accent-soft); }
  .eva .opt > button:first-child { flex: 1; display: flex; align-items: center; gap: 12px; padding: 8px; border: 0;
                                   background: transparent; text-align: left; cursor: pointer; color: var(--ink); }
  .eva .tabs { display: flex; gap: 26px; border-bottom: 1px solid var(--line); }
  .eva .tab { height: 40px; margin-bottom: -1px; padding: 0; border: 0; border-bottom: 2px solid transparent;
              background: transparent; font-size: 14px; font-weight: 500; color: var(--muted); cursor: pointer; }
  .eva .tab[aria-selected="true"] { border-bottom-color: var(--accent); color: var(--accent-dk); font-weight: 600; }
  .eva .tab-reset { margin-left: auto; font-size: 13px; }
  .eva .tab-reset[aria-selected="true"] { border-bottom-color: var(--danger); color: var(--danger); }
  .eva .switch { width: 40px; height: 24px; border-radius: 99px; border: 0; padding: 2px; display: flex;
                 background: var(--line); justify-content: flex-start; cursor: pointer; flex-shrink: 0; }
  .eva .switch[aria-checked="true"] { background: var(--accent); justify-content: flex-end; }
  .eva .switch::after { content: ""; width: 20px; height: 20px; border-radius: 99px; background: #FFFFFF;
                        box-shadow: 0 1px 2px rgba(0,0,0,.2); }
  .eva .scrim { position: fixed; inset: 0; background: rgba(66,66,74,.22); z-index: 40; }
  .eva .sheet { position: fixed; top: 0; right: 0; bottom: 0; width: 440px; max-width: 100vw; background: var(--surface);
                z-index: 50; box-shadow: -24px 0 48px rgba(66,66,74,.14); display: flex; flex-direction: column; }
  .eva .row-pick { display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 14px; border: 0;
                   border-top: 1px solid var(--soft); background: var(--surface); text-align: left; cursor: pointer; color: var(--ink); }
  .eva .row-pick:first-child { border-top: 0; }
  .eva .row-pick[aria-checked="true"] { background: var(--accent-soft); }
  .eva .row-pick:disabled { opacity: .5; cursor: default; }
</style>

<div class="eva min-h-screen">
  <div class="max-w-5xl mx-auto px-6 py-6 flex flex-col gap-6">
    <div id="status" class="text-sm" style="color: var(--muted)">Carregando contato…</div>

    <header id="contact-header" class="hidden flex flex-col gap-4">
      <div class="flex items-start gap-4">
        <div id="contact-view" class="flex-1 flex flex-col gap-1">
          <div class="flex items-center gap-1">
            <h1 id="contact-name" class="serif" style="font-size: 34px; line-height: 1.05; margin: 0"></h1>
            <button id="contact-edit-btn" type="button" class="ibtn" aria-label="Editar nome e CPF do contato">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/></svg>
            </button>
          </div>
          <span id="contact-phone" class="num" style="font-size: 14px; color: var(--muted)"></span>
        </div>
        <form id="contact-edit" class="hidden card flex-1 p-4 flex flex-col gap-3">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label class="flex flex-col gap-2"><span class="cap">Nome do contato</span><input id="c_name" class="inp"></label>
            <label class="flex flex-col gap-2"><span class="cap">CPF</span><input id="c_cpf" class="inp num"></label>
          </div>
          <div class="flex justify-end gap-2">
            <button type="button" id="contact-edit-cancel" class="btn">Cancelar</button>
            <button type="submit" class="btn btn-primary">Salvar</button>
          </div>
        </form>
        <span id="eva-status" class="hidden inline-flex text-xs mt-3 items-center gap-2 whitespace-nowrap"></span>
      </div>

      <div class="flex items-center gap-2 relative">
        <button id="patient-box" type="button" class="pbox" aria-haspopup="listbox" aria-expanded="false">
          <span id="pbox-av" class="av"></span>
          <span class="flex-1 flex flex-col" style="gap: 2px">
            <span class="cap" style="font-size: 10px">Paciente</span>
            <span id="pbox-name" style="font-size: 15px; font-weight: 500"></span>
          </span>
          <span id="pbox-count" class="num text-xs" style="color: var(--muted)"></span>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="color: var(--muted)"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        <button id="link-open" type="button" class="plus" aria-label="Vincular outro paciente a este número">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
        </button>
        <div id="patient-list" role="listbox" class="hidden card dropdown"></div>
      </div>
    </header>

    <nav id="tab-buttons" class="tabs" role="tablist" aria-label="Seções do painel">
      <button type="button" role="tab" data-tab="financeiro" class="tab" aria-selected="true">Financeiro</button>
      <button type="button" role="tab" data-tab="cadastro" class="tab" aria-selected="false">Cadastro</button>
      <button type="button" role="tab" data-tab="reset" class="tab tab-reset" aria-selected="false">Resetar</button>
    </nav>

    <section id="tab-financeiro" class="tab-panel space-y-4">
      <div id="pagamentos-box" class="hidden">
        <h2 class="serif" style="font-size: 22px; margin: 0 0 8px">Pendências</h2>
        <div id="pagamentos-list" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3"></div>
      </div>
      <div id="financeiro-forms"></div>
    </section>

    <section id="tab-cadastro" class="tab-panel hidden space-y-4">
      <div id="vinculo-card"></div>
      <div id="forms-paciente"></div>
    </section>

    <section id="tab-reset" class="tab-panel hidden">
      <div class="card p-6 flex flex-col items-start gap-3">
        <h2 class="serif" style="font-size: 22px; margin: 0">Apagar a memória da conversa</h2>
        <p class="text-sm" style="color: var(--muted)">Na próxima mensagem, a Eva relê os dados corrigidos do banco.</p>
        <button id="reset-btn" type="button" class="btn" style="color: var(--danger); border-color: var(--danger)">Resetar memória</button>
      </div>
    </section>
  </div>

  <div id="link-sheet" class="hidden">
    <div class="scrim" data-close-sheet></div>
    <aside class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
      <div class="flex items-start gap-3" style="padding: 24px 28px 18px; border-bottom: 1px solid var(--soft)">
        <div class="flex-1 flex flex-col gap-1">
          <span class="cap">Vincular a este número</span>
          <h2 id="sheet-title" class="serif" style="font-size: 26px; margin: 0">Buscar paciente</h2>
        </div>
        <button type="button" class="ibtn" data-close-sheet aria-label="Fechar">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
        </button>
      </div>
      <div class="flex-1 overflow-auto flex flex-col gap-5" style="padding: 22px 28px">
        <label class="flex flex-col gap-2"><span class="cap">Nome do paciente</span>
          <input id="link-q" class="inp" autocomplete="off" placeholder="3 letras ou mais"></label>
        <div id="link-results" class="card overflow-hidden hidden" role="radiogroup" aria-label="Pacientes encontrados"></div>
        <p id="link-empty" class="hidden text-sm" style="color: var(--muted)">Ninguém encontrado.</p>
        <button id="new-open" type="button" class="text-sm font-medium text-left" style="color: var(--accent); background: none; border: 0; padding: 0; cursor: pointer">+ Criar ficha nova</button>
        <form id="new-form" class="hidden card p-4 flex flex-col gap-3">
          <label class="flex flex-col gap-2"><span class="cap">Nome completo</span><input id="new-name" class="inp"></label>
          <label class="flex flex-col gap-2"><span class="cap">Nascimento</span><input id="new-birth" class="inp num" placeholder="dd/mm/aaaa"></label>
          <div class="flex justify-end gap-2">
            <button type="button" id="new-cancel" class="btn">Cancelar</button>
            <button type="submit" class="btn btn-primary">Criar ficha</button>
          </div>
        </form>
        <p id="new-msg" class="hidden text-sm"></p>
        <div class="card flex items-center gap-3" style="padding: 12px 14px">
          <span class="flex-1 text-sm">Número do próprio paciente</span>
          <button id="link-self" type="button" class="switch" role="switch" aria-checked="false" aria-label="Número do próprio paciente"></button>
        </div>
        <label id="link-rel-wrap" class="flex flex-col gap-2"><span class="cap">Parentesco</span>
          <select id="link-rel" class="inp">
            <option value="">Escolha…</option>
            {% for r in relationships %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
          </select>
        </label>
        <p id="link-error" class="hidden text-sm" style="color: var(--danger)"></p>
      </div>
      <div class="flex justify-end" style="padding: 16px 28px 22px; border-top: 1px solid var(--soft)">
        <button id="link-submit" type="button" class="btn btn-primary" disabled>Vincular</button>
      </div>
    </aside>
  </div>
</div>

<script>
```

- [ ] **Step 3: Escreva o script novo logo depois de `<script>`**

```js
const TOKEN = {{ token | tojson }};
const RELATIONSHIPS = {{ relationships | tojson }};
let PHONE = null;
let CONTACT = null;
let CONVERSATION_ID = null;
let AGENT = "";
let PATIENTS = [];
let CURRENT_PID = null;
let LINK_PID = null;
let SEARCH_TIMER = null;

const $ = (id) => document.getElementById(id);
const api = (path, params = {}) => `${path}?${new URLSearchParams({ ...params, token: TOKEN })}`;
function postJson(path, body) {
  return fetch(api(path), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
const errText = (body, fallback) => (body && typeof body.detail === "string" ? body.detail : fallback);

// ── 0. Abas ──────────────────────────────────────────────────────────────────
function showTab(name) {
  document.querySelectorAll(".tab-panel").forEach((el) => el.classList.toggle("hidden", el.id !== `tab-${name}`));
  document.querySelectorAll("#tab-buttons [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
}
document.querySelectorAll("#tab-buttons [role=tab]").forEach((b) => { b.onclick = () => showTab(b.dataset.tab); });

// ── 1. Obter telefone: Chatwoot postMessage ou ?phone= (teste) ──────────────
function initPhone() {
  const qp = new URLSearchParams(location.search).get("phone");
  if (qp) { PHONE = qp; load(); return; }
  window.addEventListener("message", (e) => {
    try {
      const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
      if (data && data.event === "appContext" && data.data && data.data.contact) {
        PHONE = data.data.contact.phone_number || PHONE;
        if (data.data.conversation && data.data.conversation.id) CONVERSATION_ID = data.data.conversation.id;
        if (data.data.currentAgent && data.data.currentAgent.name) AGENT = data.data.currentAgent.name;
        if (PHONE) load();
      }
    } catch (_) {}
  });
  window.parent.postMessage("chatwoot-dashboard-app:fetch-info", "*");
}

function setStatus(t) { $("status").textContent = t; }
function flash(msg) { setStatus(msg); setTimeout(() => setStatus(""), 1500); }

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const FEM = ["mãe", "avó", "tia", "irmã", "madrasta"];
const MASC = ["pai", "avô", "tio", "irmão", "padrasto"];
function relLabel(link) {
  if (!link) return "";
  if (link.is_self) return "próprio número";
  const r = (link.relationship || "").trim();
  if (!r) return "parentesco não informado";
  return `número ${FEM.includes(r) ? "da" : MASC.includes(r) ? "do" : "de"} ${r}`;
}
function initials(name) {
  return (name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("");
}

// ── 2. Contato + pacientes ───────────────────────────────────────────────────
async function load(selectPid) {
  setStatus("Buscando contato…");
  const r = await fetch(api("/api/atendente/resolve", { phone: PHONE }));
  if (!r.ok) { setStatus("Erro ao buscar (token?)."); return; }
  const { contact, patients } = await r.json();
  CONTACT = contact;
  if (!contact) { setStatus("Nenhum contato encontrado para este número."); return; }
  setStatus("");
  $("contact-header").classList.remove("hidden");
  renderContact();
  PATIENTS = patients;
  loadPagamentos();
  const pid = patients.some((p) => p.id === selectPid) ? selectPid : (patients[0] ? patients[0].id : null);
  selectPatient(pid);
}

function renderContact() {
  $("contact-name").textContent = CONTACT.name || "Sem nome";
  $("contact-phone").textContent = CONTACT.phone ? `+${CONTACT.phone}` : "";
}

$("contact-edit-btn").onclick = () => {
  $("c_name").value = CONTACT.name || "";
  $("c_cpf").value = CONTACT.cpf || "";
  $("contact-view").classList.add("hidden");
  $("contact-edit").classList.remove("hidden");
  $("c_name").focus();
};
function closeContactEdit() {
  $("contact-edit").classList.add("hidden");
  $("contact-view").classList.remove("hidden");
}
$("contact-edit-cancel").onclick = closeContactEdit;
$("contact-edit").onsubmit = async (e) => {
  e.preventDefault();
  const data = { name: val("c_name").trim(), cpf: val("c_cpf").trim() };
  if (await post(`/api/atendente/contato/${CONTACT.id}`, { data })) {
    CONTACT = { ...CONTACT, ...data };
    renderContact();
    closeContactEdit();
    flash("Contato salvo ✓");
  }
};

// ── 3. Caixa de paciente ─────────────────────────────────────────────────────
function selectPatient(pid) {
  CURRENT_PID = pid;
  const p = PATIENTS.find((x) => x.id === pid);
  $("pbox-av").textContent = p ? initials(p.name) : "+";
  $("pbox-name").innerHTML = p
    ? `${escapeHtml(p.name)} <span style="font-weight:400;color:var(--muted)">· ${escapeHtml(relLabel(p.link))}</span>`
    : `<span style="color:var(--muted)">Nenhum paciente vinculado</span>`;
  $("pbox-count").textContent = PATIENTS.length > 1 ? `${PATIENTS.length} vinculados` : "";
  renderPatientList();
  if (p) { loadPatient(pid); } else { renderEvaStatus(null); renderVinculo(null); renderForms(null); }
}

function renderPatientList() {
  $("patient-list").innerHTML = PATIENTS.map((p) => `
    <div class="opt" role="option" aria-selected="${p.id === CURRENT_PID}">
      <button type="button" data-pick="${escapeHtml(p.id)}">
        <span class="av" style="width:32px;height:32px">${escapeHtml(initials(p.name))}</span>
        <span class="flex flex-col">
          <span class="text-sm font-medium">${escapeHtml(p.name)}</span>
          <span class="text-xs" style="color:var(--muted)">${escapeHtml(relLabel(p.link))}</span>
        </span>
      </button>
      <button type="button" class="ibtn" data-unlink="${escapeHtml(p.id)}" aria-label="Desvincular ${escapeHtml(p.name)}">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M9 17H7a5 5 0 0 1 0-10h2M15 7h2a5 5 0 0 1 0 10h-2M4 20 20 4"/></svg>
      </button>
    </div>`).join("") || `<p class="text-sm p-3" style="color:var(--muted)">Nenhum paciente vinculado.</p>`;
}

function setListOpen(open) {
  $("patient-list").classList.toggle("hidden", !open);
  $("patient-box").setAttribute("aria-expanded", String(open));
}
$("patient-box").onclick = () => setListOpen($("patient-list").classList.contains("hidden"));
$("patient-list").onclick = async (e) => {
  const pick = e.target.closest("[data-pick]");
  const un = e.target.closest("[data-unlink]");
  if (pick) { setListOpen(false); selectPatient(pick.dataset.pick); }
  if (un) await unlink(un.dataset.unlink);
};
document.addEventListener("click", (e) => {
  if (!e.target.closest("#patient-box, #patient-list")) setListOpen(false);
});

async function unlink(pid) {
  const p = PATIENTS.find((x) => x.id === pid);
  if (!confirm(`Desvincular ${p ? p.name : "este paciente"} deste número?`)) return;
  const r = await postJson("/api/atendente/desvincular", { phone: PHONE, patient_id: pid, agent: AGENT });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) { alert(errText(body, "Erro ao desvincular.")); return; }
  setListOpen(false);
  flash("Desvinculado ✓");
  load(pid === CURRENT_PID ? null : CURRENT_PID);
}

// ── 4. Paciente selecionado ──────────────────────────────────────────────────
async function loadPatient(pid) {
  const r = await fetch(api(`/api/atendente/paciente/${pid}`, { contact_id: CONTACT.id }));
  if (!r.ok) { setStatus("Erro ao carregar paciente."); return; }
  const { patient, link, return_reminder, eva_off } = await r.json();
  renderEvaStatus(eva_off);
  renderVinculo(link);
  renderForms(patient, return_reminder, eva_off);
}

function renderEvaStatus(off) {
  const el = $("eva-status");
  if (off === null) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  el.innerHTML = `<span style="width:6px;height:6px;border-radius:99px;background:${off ? "var(--danger)" : "var(--ok)"}"></span>${off ? "Eva desligada" : "Eva ativa"}`;
}

function relOptions(selected) {
  return RELATIONSHIPS.map((r) => `<option value="${escapeHtml(r)}" ${r === selected ? "selected" : ""}>${escapeHtml(r)}</option>`).join("");
}

function toggleSwitch(swId, relWrapId) {
  const sw = $(swId);
  const on = sw.getAttribute("aria-checked") !== "true";
  sw.setAttribute("aria-checked", String(on));
  $(relWrapId).classList.toggle("hidden", on);
}

function renderVinculo(link) {
  const box = $("vinculo-card");
  if (!link) { box.innerHTML = ""; return; }
  const isSelf = !!link.is_self;
  const rel = (link.relationship || "").trim();
  const legacy = !isSelf && rel && !RELATIONSHIPS.includes(rel);
  box.innerHTML = `<section class="card p-4 flex flex-col gap-3">
    <span class="cap">Vínculo com este número</span>
    <div class="flex items-center gap-3">
      <span class="flex-1 text-sm">Número do próprio paciente</span>
      <button id="v-self" type="button" class="switch" role="switch" aria-checked="${isSelf}" aria-label="Número do próprio paciente"></button>
    </div>
    <label id="v-rel-wrap" class="flex flex-col gap-2 ${isSelf ? "hidden" : ""}">
      <span class="cap">Parentesco</span>
      <select id="v-rel" class="inp"><option value="">Escolha…</option>${relOptions(legacy ? "acompanhante" : rel)}</select>
      ${legacy ? `<span class="text-xs" style="color:var(--muted)">Antes estava escrito "${escapeHtml(rel)}". Confira e salve.</span>` : ""}
    </label>
    <button type="button" class="btn btn-primary self-end" id="v-save">Salvar vínculo</button>
  </section>`;
  $("v-self").onclick = () => toggleSwitch("v-self", "v-rel-wrap");
  $("v-save").onclick = () => saveLink(link.id);
}

async function saveLink(pcid) {
  const isSelf = $("v-self").getAttribute("aria-checked") === "true";
  const relationship = isSelf ? null : val("v-rel");
  if (!isSelf && !relationship) { alert("Escolha o parentesco."); return; }
  if (await post(`/api/atendente/vinculo/${pcid}`, { data: { is_self: isSelf, relationship } })) {
    flash("Vínculo salvo ✓");
    load(CURRENT_PID);
  }
}

// ── 5. Folha de vínculo ──────────────────────────────────────────────────────
function openLinkSheet() {
  LINK_PID = null;
  $("link-q").value = "";
  $("link-results").innerHTML = "";
  $("link-results").classList.add("hidden");
  $("link-empty").classList.add("hidden");
  $("new-form").classList.add("hidden");
  $("new-msg").classList.add("hidden");
  $("link-self").setAttribute("aria-checked", "false");
  $("link-rel-wrap").classList.remove("hidden");
  $("link-rel").value = "";
  $("link-error").classList.add("hidden");
  updateLinkSubmit();
  setListOpen(false);
  $("link-sheet").classList.remove("hidden");
  $("link-q").focus();
}
function closeLinkSheet() { $("link-sheet").classList.add("hidden"); }
$("link-open").onclick = openLinkSheet;
document.querySelectorAll("[data-close-sheet]").forEach((el) => { el.onclick = closeLinkSheet; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("link-sheet").classList.contains("hidden")) closeLinkSheet();
});

function updateLinkSubmit() {
  const isSelf = $("link-self").getAttribute("aria-checked") === "true";
  $("link-submit").disabled = !LINK_PID || (!isSelf && !$("link-rel").value);
}
$("link-self").onclick = () => { toggleSwitch("link-self", "link-rel-wrap"); updateLinkSubmit(); };
$("link-rel").onchange = updateLinkSubmit;

$("link-q").oninput = () => { clearTimeout(SEARCH_TIMER); SEARCH_TIMER = setTimeout(searchPatients, 250); };
async function searchPatients() {
  const q = $("link-q").value.trim();
  LINK_PID = null;
  updateLinkSubmit();
  if (q.length < 3) {
    $("link-results").classList.add("hidden");
    $("link-empty").classList.add("hidden");
    return;
  }
  const r = await fetch(api("/api/atendente/pacientes/busca", { q, phone: PHONE }));
  if (!r.ok) return;
  const hits = await r.json();
  if (q !== $("link-q").value.trim()) return;  // chegou resposta de uma busca antiga
  renderResults(hits);
}

function renderResults(hits) {
  const box = $("link-results");
  const linked = new Set(PATIENTS.map((p) => p.id));
  $("link-empty").classList.toggle("hidden", hits.length > 0);
  box.classList.toggle("hidden", hits.length === 0);
  box.innerHTML = hits.map((h) => {
    const extra = [h.birth_date || "sem nascimento", h.phone_hint ? `final ${h.phone_hint}` : "", linked.has(h.id) ? "já vinculado" : ""]
      .filter(Boolean).join(" · ");
    return `<button type="button" class="row-pick" role="radio" aria-checked="false" data-pid="${escapeHtml(h.id)}" ${linked.has(h.id) ? "disabled" : ""}>
      <span class="flex-1 text-sm font-medium">${escapeHtml(h.name)}</span>
      <span class="num text-xs" style="color:var(--muted)">${escapeHtml(extra)}</span>
    </button>`;
  }).join("");
}
$("link-results").onclick = (e) => {
  const row = e.target.closest("[data-pid]");
  if (row && !row.disabled) pickLinkPatient(row.dataset.pid);
};
function pickLinkPatient(pid) {
  LINK_PID = pid;
  $("link-results").querySelectorAll("[data-pid]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.pid === pid)));
  updateLinkSubmit();
}

$("new-open").onclick = () => {
  $("new-form").classList.remove("hidden");
  $("new-msg").classList.add("hidden");
  $("new-name").value = $("link-q").value.trim();
  $("new-birth").value = "";
  $("new-name").focus();
};
$("new-cancel").onclick = () => $("new-form").classList.add("hidden");
$("new-form").onsubmit = async (e) => {
  e.preventDefault();
  const msg = $("new-msg");
  const r = await postJson("/api/atendente/paciente-novo", {
    phone: PHONE, name: val("new-name"), birth_date: val("new-birth"), agent: AGENT,
  });
  const body = await r.json().catch(() => ({}));
  msg.classList.remove("hidden");
  if (r.status === 409) {
    const dups = body.detail.duplicates;
    renderResults(dups.map((d) => ({ ...d, phone_hint: null })));
    pickLinkPatient(dups[0].id);
    $("new-form").classList.add("hidden");
    msg.style.color = "var(--danger)";
    msg.textContent = "Já existe ficha com este nome e nascimento. Selecionei a ficha existente.";
    return;
  }
  if (!r.ok) { msg.style.color = "var(--danger)"; msg.textContent = errText(body, "Erro ao criar ficha."); return; }
  renderResults([{ ...body.patient, phone_hint: null }]);
  pickLinkPatient(body.patient.id);
  $("new-form").classList.add("hidden");
  msg.style.color = "var(--ok)";
  msg.textContent = "Ficha criada. Agora é só vincular.";
};

$("link-submit").onclick = async () => {
  const isSelf = $("link-self").getAttribute("aria-checked") === "true";
  const r = await postJson("/api/atendente/vinculo", {
    phone: PHONE, patient_id: LINK_PID, is_self: isSelf,
    relationship: isSelf ? null : $("link-rel").value, agent: AGENT,
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    $("link-error").textContent = errText(body, "Erro ao vincular.");
    $("link-error").classList.remove("hidden");
    return;
  }
  const pid = LINK_PID;
  closeLinkSheet();
  flash("Paciente vinculado ✓");
  load(pid);
};
```

- [ ] **Step 4: Copie as funções mantidas, sem mudança**

Logo depois do script novo, copie de `/tmp/atendente.old.html`, na mesma ordem e **sem alterar**, estes trechos:

1. Seção `// ── 2b. Pagamentos pendentes` inteira: `loadPagamentos`, `pagamentoCard`, o `document.addEventListener("change", ...)`, `marcarPago`, `marcarNoShowPag`, `isentarTaxa` (hoje linhas 110 a 239).
2. De `// ── 4. Renderizar formulários` até o fim de `evaBlock`: `field`, `checkbox`, `select`, `DOCTORS`, `RETURN_LABELS`, `returnBlock`, `evaBlock` (hoje linhas 275 a 337).
3. De `// ── 5. Salvar` até o fim de `saveFinanceiro`: `post`, `val`, `chk`, `numOrNull`, `saveContact`, `savePatient`, `saveFinanceiro` (hoje linhas 401 a 437). Depois, apague `saveContact` (o topo salva o contato agora).
4. `saveRetorno` e `setEva` (hoje linhas 444 a 457).

Não copie: `load`, `loadPatient`, `renderForms`, `saveLink`, `flash`, `showTab`, `initPhone`, `setStatus`, `escapeHtml` antigos (já estão no script novo) nem o handler de reset (vem no Step 5).

Em `loadPagamentos` copiada, troque as duas URLs montadas à mão por `api(...)`:

```js
  const r = await fetch(api("/api/atendente/pagamentos", { phone: PHONE }));
```

As demais funções copiadas continuam usando `?token=${encodeURIComponent(TOKEN)}` e funcionam como estão.

- [ ] **Step 5: Escreva o `renderForms` novo, o reset e o fechamento**

Depois das funções copiadas:

```js
function renderForms(patient, returnReminder, evaOff) {
  if (!patient) {
    $("forms-paciente").innerHTML = "";
    $("financeiro-forms").innerHTML = "";
    return;
  }
  $("forms-paciente").innerHTML = `<section class="bg-white dark:bg-gray-800 rounded-lg shadow p-4" data-pid="${patient.id}">
      <h2 class="font-medium text-gray-700 dark:text-gray-100 mb-2">Ficha</h2>
      <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        ${field("Nome", "p_name", patient.name)}
        ${field("Nome Social", "p_social_name", patient.social_name)}
        ${field("Nascimento (dd/mm/aaaa)", "p_birth", patient.birth_date)}
        ${field("CPF", "p_cpf", patient.patient_cpf)}
        ${field("E-mail", "p_email", patient.email)}
        ${select("Médico", "p_doctor", patient.doctor_id || "", DOCTORS)}
        ${select("Modalidade", "p_modality", patient.modality_restriction || "", [["", "—"], ["online", "Online"], ["presencial", "Presencial"]])}
        ${field("Preço custom (R$)", "p_price", patient.custom_price, "number")}
      </div>
      <div class="flex flex-wrap gap-4 mt-1 mb-2">
        ${checkbox("Paciente retornante", "p_returning", patient.is_returning_patient)}
        ${checkbox("Exceção de idade", "p_age_exc", patient.age_exception)}
        ${checkbox("Taxa de reserva sempre isenta", "p_fee_waived", patient.booking_fee_waived)}
      </div>
      ${returnBlock(patient.id, returnReminder)}
      ${evaBlock(patient.id, evaOff)}
      <button onclick="savePatient('${patient.id}')" class="mt-2 bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded">Salvar paciente</button>
    </section>`;
  $("financeiro-forms").innerHTML = `<section class="bg-white dark:bg-gray-800 rounded-lg shadow p-4" data-pid="${patient.id}">
      <h2 class="font-medium text-gray-700 dark:text-gray-100 mb-2">Dados financeiros</h2>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        ${field("Financeiro — nome", "p_fin_name", patient.financial_name)}
        ${field("Financeiro — CPF", "p_fin_cpf", patient.financial_cpf)}
        ${field("Financeiro — e-mail", "p_fin_email", patient.financial_email)}
      </div>
      <button onclick="saveFinanceiro('${patient.id}')" class="mt-2 bg-wa-green hover:bg-wa-green-dk text-white text-xs px-3 py-1.5 rounded">Salvar financeiro</button>
    </section>`;
}

// ── 6. Reset ─────────────────────────────────────────────────────────────────
$("reset-btn").onclick = async () => {
  if (!confirm("Resetar a memória da Eva para este número? A conversa será esquecida (mensagens permanecem).")) return;
  const r = await postJson("/api/atendente/reset-checkpoint", { phone: PHONE });
  if (!r.ok) { alert("Erro ao resetar."); return; }
  const { deleted } = await r.json();
  flash(`Memória resetada (${deleted} linhas) ✓`);
};

initPhone();
</script>
{% endblock %}
```

(O visual novo das abas Financeiro, Cadastro e Resetar é a Parte 4; aqui o conteúdo delas continua o de hoje.)

- [ ] **Step 6: Run the dashboard tests**

Run: `cd dashboard && uv run pytest -q`
Expected: PASS, incluindo `test_atendente_renderiza_moldura_e_lista_de_parentesco` e os testes de `/atendente` que já existiam.

- [ ] **Step 7: Verifique a sintaxe do JS**

Run:
```bash
cd dashboard && uv run python -c "
import re, jinja2, pathlib
src = pathlib.Path('templates/atendente.html').read_text()
js = re.search(r'<script>(.*)</script>', src, re.S).group(1)
js = js.replace('{{ token | tojson }}', '\"t\"').replace('{{ relationships | tojson }}', '[]')
pathlib.Path('/tmp/atendente.js').write_text(js)
" && node --check /tmp/atendente.js && echo JS_OK
```
Expected: `JS_OK`

- [ ] **Step 8: Commit**

```bash
git add dashboard/main.py dashboard/templates/atendente.html dashboard/tests/test_main_auth.py
git commit -m "feat(painel): moldura nova com contato no topo, caixa de paciente e folha de vínculo"
```

---

### Task 12: Verificação final e PR

- [ ] **Step 1: Suítes completas**

Run: `cd dashboard && uv run pytest -q` e depois `uv run pytest --tb=short` na raiz.
Expected: tudo PASS.

- [ ] **Step 2: Conferência na tela**

O Supabase não resolve DNS no Mac local (ver memória "DNS do Supabase recusado"), então a tela é conferida com o banco falso. Suba o painel com um banco em memória e abra no navegador do app:

```bash
cd dashboard && ATTENDANT_PANEL_TOKEN=dev DASHBOARD_PASSWORD=dev-senha SUPABASE_URL=http://fake SUPABASE_KEY=fake uv run python -c "
import asyncio, uvicorn, db_client, main
from tests.conftest import FakeClient
store = {
 'contacts': [{'id': 'c1', 'phone': '5581999995432', 'name': 'Carla Menezes', 'cpf': ''}],
 'patients': [{'id': 'p1', 'name': 'Lucas Menezes', 'birth_date': '14/03/2017'},
              {'id': 'p2', 'name': 'João Menezes', 'birth_date': '06/05/2014'}],
 'patient_contacts': [{'id': 'pc1', 'contact_id': 'c1', 'patient_id': 'p1', 'role': 'agendamento',
                       'is_self': False, 'relationship': 'mãe', 'patients': {'id': 'p1', 'name': 'Lucas Menezes', 'birth_date': '14/03/2017'}}],
}
fake = FakeClient(store)
async def get_client(): return fake
import attendant_db; attendant_db.get_client = get_client
uvicorn.run(main.app, port=8765)
"
```

Abra `http://localhost:8765/atendente?token=dev&phone=5581999995432` e confira: nome da Carla no topo e o lápis abrindo nome e CPF; caixa "Lucas Menezes · número da mãe"; "+" abre a folha; buscar "joao" acha o João; "Número do próprio paciente" esconde o parentesco; vincular faz o João aparecer na lista da caixa; desvincular pede confirmação. (O banco falso não faz o join `patients(*)` do vínculo novo, então o João recém-vinculado pode aparecer sem nome na caixa; isso é limitação do banco falso, não da tela.)

- [ ] **Step 3: Abrir o PR**

Confira `git log origin/main..HEAD` (só commits desta parte) e abra o PR para `main` com o resumo das tarefas e a lista de conferência do Step 2.
