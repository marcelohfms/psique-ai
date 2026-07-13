# Lembretes de Retorno Periódico Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar o médico classificar, num dashboard novo, de quanto em quanto tempo cada paciente deve retornar (15 dias / 1 / 3 / 6 meses), e enviar 3 lembretes automáticos de WhatsApp (um mês antes / no mês / atrasado) conforme essa data calculada se aproxima.

**Architecture:** Nova tabela `return_reminders` (1 linha por paciente, separada de `patients`) guarda intervalo + `next_return_date` + 3 flags de envio. Um dashboard novo (`/retornos`, `dashboard/`) deixa o médico classificar pacientes vistos hoje ou com consulta concluída ainda não classificada. Um cron diário novo (`scripts/send_return_reminders.py`) lê `return_reminders`, decide por mês calendário qual dos 3 templates disparar, e envia em lotes throttled via WhatsApp/Chatwoot.

**Tech Stack:** FastAPI (dashboard), Supabase (Postgres), Jinja2 + Tailwind (UI), Meta WhatsApp templates via Chatwoot, GitHub Actions (cron), pytest.

**Spec:** [docs/superpowers/specs/2026-07-13-lembretes-retorno-design.md](../specs/2026-07-13-lembretes-retorno-design.md)

---

## Task 1: Migration — tabela `return_reminders`

**Files:**
- Create: `supabase/migrations/20260714_create_return_reminders.sql`

- [ ] **Step 1: Escrever a migration**

```sql
-- Cria return_reminders: rastreia a classificação de retorno periódico do
-- paciente (definida pelo médico no dashboard /retornos) e os lembretes de
-- WhatsApp já enviados no ciclo atual. Separada de `patients` de propósito —
-- patients continua focada em dado do paciente em si.

CREATE TABLE IF NOT EXISTS return_reminders (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id                      UUID NOT NULL UNIQUE REFERENCES patients(id) ON DELETE CASCADE,
    doctor_id                       UUID NOT NULL REFERENCES doctors(doctor_id),
    return_interval                 TEXT NOT NULL CHECK (return_interval IN ('15_dias','1_mes','3_meses','6_meses')),
    next_return_date                DATE NOT NULL,
    last_classified_appointment_id  UUID REFERENCES appointments(appointment_id),
    month_before_sent_at            TIMESTAMPTZ,
    month_of_sent_at                TIMESTAMPTZ,
    overdue_sent_at                 TIMESTAMPTZ,
    updated_at                      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_return_reminders_patient ON return_reminders(patient_id);

-- Mesma postura defensiva da migration 20260713 (RLS habilitado, zero
-- policies): fecha a porta pra chave anon, sem mudar o comportamento do
-- backend (que usa service_role).
ALTER TABLE IF EXISTS return_reminders ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 2: Aplicar a migration no Supabase**

Rode o SQL acima no editor SQL do Supabase (produção), do mesmo jeito que as
migrations anteriores em `supabase/migrations/` foram aplicadas manualmente
(não há runner automático neste projeto — confirme olhando
`supabase/migrations/20260713_enable_rls_patient_tables.sql` como referência
do padrão já usado).

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260714_create_return_reminders.sql
git commit -m "feat(db): cria tabela return_reminders"
```

---

## Task 2: `dashboard/return_reminders.py` — cálculo de `next_return_date`

**Files:**
- Create: `dashboard/return_reminders.py`
- Test: `dashboard/tests/test_return_reminders.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
# dashboard/tests/test_return_reminders.py
from datetime import date

import return_reminders as rr


def test_compute_next_return_date_15_dias():
    assert rr.compute_next_return_date(date(2026, 7, 13), "15_dias") == date(2026, 7, 28)


def test_compute_next_return_date_1_mes():
    assert rr.compute_next_return_date(date(2026, 7, 13), "1_mes") == date(2026, 8, 13)


def test_compute_next_return_date_3_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "3_meses") == date(2026, 10, 13)


def test_compute_next_return_date_6_meses():
    assert rr.compute_next_return_date(date(2026, 7, 13), "6_meses") == date(2027, 1, 13)


def test_compute_next_return_date_dia_31_cai_pro_ultimo_dia_do_mes_curto():
    # 31/01 + 1 mês -> fevereiro só tem 28 dias em 2026 (não bissexto)
    assert rr.compute_next_return_date(date(2026, 1, 31), "1_mes") == date(2026, 2, 28)


def test_compute_next_return_date_interval_invalido():
    import pytest
    with pytest.raises(ValueError):
        rr.compute_next_return_date(date(2026, 7, 13), "2_meses")
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'return_reminders'`

- [ ] **Step 3: Implementar `compute_next_return_date`**

```python
# dashboard/return_reminders.py
"""Classificação de retorno periódico do paciente (dashboard /retornos).

Autocontido: não importa app/ (a imagem Docker do dashboard não contém app/).
"""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from payments import DOCTOR_DISPLAY, DOCTOR_KEY

_TZ = ZoneInfo("America/Recife")

RETURN_INTERVALS = ("15_dias", "1_mes", "3_meses", "6_meses")

RETURN_INTERVAL_LABELS = {
    "15_dias": "15 dias",
    "1_mes": "1 mês",
    "3_meses": "3 meses",
    "6_meses": "6 meses",
}

DOCTOR_ID_BY_KEY = {key: doctor_id for doctor_id, key in DOCTOR_KEY.items()}


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def compute_next_return_date(appointment_date: date, return_interval: str) -> date:
    """Calcula next_return_date a partir da data da consulta e do intervalo escolhido pelo médico."""
    if return_interval == "15_dias":
        return appointment_date + timedelta(days=15)
    if return_interval == "1_mes":
        return _add_months(appointment_date, 1)
    if return_interval == "3_meses":
        return _add_months(appointment_date, 3)
    if return_interval == "6_meses":
        return _add_months(appointment_date, 6)
    raise ValueError(f"return_interval inválido: {return_interval!r}")
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add dashboard/return_reminders.py dashboard/tests/test_return_reminders.py
git commit -m "feat(dashboard): calcula next_return_date por intervalo de retorno"
```

---

## Task 3: `dashboard/return_reminders.py` — listas "Hoje" e "Pendentes de classificação"

**Files:**
- Modify: `dashboard/tests/conftest.py` (estender `FakeQuery` com `gte`/`lt`/`neq`)
- Modify: `dashboard/return_reminders.py`
- Test: `dashboard/tests/test_return_reminders.py`

- [ ] **Step 1: Estender o `FakeQuery` do conftest com os filtros que faltam**

O `FakeQuery` em `dashboard/tests/conftest.py` hoje só sabe `eq`/`in_`. As
novas queries precisam de `gte`/`lt` (janela de data de "hoje") e `neq`
(usado depois, no cron). Adicione ao `FakeQuery`:

```python
    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def gt(self, col, val):
        self._filters.append(("gt", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self
```

E em `_matches`, adicione os casos correspondentes:

```python
            if kind == "gte" and not (row.get(col) is not None and row.get(col) >= val):
                return False
            if kind == "lt" and not (row.get(col) is not None and row.get(col) < val):
                return False
            if kind == "gt" and not (row.get(col) is not None and row.get(col) > val):
                return False
            if kind == "neq" and row.get(col) == val:
                return False
```

Rode a suíte de pagamentos pra confirmar que nada quebrou (essa classe é
compartilhada):

Run: `cd dashboard && uv run pytest tests/test_payments.py -v`
Expected: PASS (sem regressão)

- [ ] **Step 2: Escrever os testes que falham**

```python
# dashboard/tests/test_return_reminders.py (acrescentar ao final do arquivo)
JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"


def _appt(appointment_id, patient_id, patient_name, doctor_id=JULIO_ID, **overrides):
    row = {
        "appointment_id": appointment_id,
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-07-13T14:00:00+00:00",
        "status": "scheduled",
        "patients": {"name": patient_name},
    }
    row.update(overrides)
    return row


# ── get_today_appointments ────────────────────────────────────────────────


async def test_get_today_appointments_filtra_por_medico_e_dia(fake_client, monkeypatch):
    monkeypatch.setattr(rr, "_TZ", rr._TZ)  # no-op, mantém import explícito
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, start_time="2026-07-13T12:00:00+00:00"),
        _appt("a2", "p2", "Maria", doctor_id=BRUNA_ID, start_time="2026-07-13T12:00:00+00:00"),
        _appt("a3", "p3", "Ana", doctor_id=JULIO_ID, start_time="2026-07-14T12:00:00+00:00"),
    ]
    out = await rr.get_today_appointments(fake_client, JULIO_ID, today=date(2026, 7, 13))
    assert {a["appointment_id"] for a in out} == {"a1"}


# ── get_pending_classification ────────────────────────────────────────────


async def test_get_pending_classification_sem_return_reminders_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert {a["appointment_id"] for a in out} == {"a1"}


async def test_get_pending_classification_ja_classificada_nao_aparece(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    fake_client.store["return_reminders"] = [
        {"patient_id": "p1", "last_classified_appointment_id": "a1"},
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert out == []


async def test_get_pending_classification_nova_consulta_reabre_pendencia(fake_client):
    # p1 foi classificado com base em a1, mas depois teve a2 (mais recente,
    # ainda não classificada) -> deve reaparecer usando a2.
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-06-01T12:00:00+00:00"),
        _appt("a2", "p1", "João", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    fake_client.store["return_reminders"] = [
        {"patient_id": "p1", "last_classified_appointment_id": "a1"},
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert [a["appointment_id"] for a in out] == ["a2"]


async def test_get_pending_classification_ordenado_do_mais_antigo(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", status="completed", start_time="2026-07-05T12:00:00+00:00"),
        _appt("a2", "p2", "Maria", status="completed", start_time="2026-07-01T12:00:00+00:00"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert [a["appointment_id"] for a in out] == ["a2", "a1"]


async def test_get_pending_classification_filtra_por_medico(fake_client):
    fake_client.store["appointments"] = [
        _appt("a1", "p1", "João", doctor_id=JULIO_ID, status="completed"),
        _appt("a2", "p2", "Maria", doctor_id=BRUNA_ID, status="completed"),
    ]
    out = await rr.get_pending_classification(fake_client, JULIO_ID)
    assert {a["appointment_id"] for a in out} == {"a1"}
```

Adicione o import no topo do arquivo de teste:

```python
from datetime import date

import return_reminders as rr
```

- [ ] **Step 3: Rodar os testes e confirmar que falham**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: FAIL com `AttributeError: module 'return_reminders' has no attribute 'get_today_appointments'`

- [ ] **Step 4: Implementar `get_today_appointments` e `get_pending_classification`**

Acrescente ao final de `dashboard/return_reminders.py`:

```python
async def get_today_appointments(client, doctor_id: str, today: date | None = None) -> list[dict]:
    """Pacientes com consulta hoje para o médico — sempre visível, para classificar na hora."""
    if today is None:
        today = datetime.now(_TZ).date()
    start = datetime(today.year, today.month, today.day, tzinfo=_TZ)
    end = start + timedelta(days=1)
    result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .gte("start_time", start.isoformat())
        .lt("start_time", end.isoformat())
        .order("start_time")
        .execute()
    )
    return result.data or []


async def get_pending_classification(client, doctor_id: str) -> list[dict]:
    """Pacientes cuja consulta concluída mais recente ainda não foi classificada.

    Ordenados da mais antiga para a mais nova (fila que vai esvaziando).
    """
    appts_result = await (
        client.from_("appointments")
        .select("appointment_id, start_time, patient_id, patients(name)")
        .eq("doctor_id", doctor_id)
        .eq("status", "completed")
        .order("start_time")
        .execute()
    )
    latest_by_patient: dict[str, dict] = {}
    for appt in appts_result.data or []:
        patient_id = appt.get("patient_id")
        if not patient_id:
            continue
        current = latest_by_patient.get(patient_id)
        if current is None or appt["start_time"] > current["start_time"]:
            latest_by_patient[patient_id] = appt

    if not latest_by_patient:
        return []

    rr_result = await (
        client.from_("return_reminders")
        .select("patient_id, last_classified_appointment_id")
        .in_("patient_id", list(latest_by_patient.keys()))
        .execute()
    )
    classified = {
        row["patient_id"]: row.get("last_classified_appointment_id")
        for row in (rr_result.data or [])
    }

    pending = [
        appt for appt in latest_by_patient.values()
        if classified.get(appt["patient_id"]) != appt["appointment_id"]
    ]
    pending.sort(key=lambda a: a["start_time"])
    return pending
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: PASS (12 testes no total)

- [ ] **Step 6: Commit**

```bash
git add dashboard/tests/conftest.py dashboard/return_reminders.py dashboard/tests/test_return_reminders.py
git commit -m "feat(dashboard): lista pacientes de hoje e pendentes de classificação de retorno"
```

---

## Task 4: `dashboard/return_reminders.py` — `save_classification`

**Files:**
- Modify: `dashboard/return_reminders.py`
- Test: `dashboard/tests/test_return_reminders.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
# dashboard/tests/test_return_reminders.py (acrescentar ao final)

# ── save_classification ───────────────────────────────────────────────────


async def test_save_classification_cria_linha_nova(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="3_meses",
    )
    assert saved["patient_id"] == "p1"
    assert saved["return_interval"] == "3_meses"
    assert saved["next_return_date"] == "2026-10-13"
    assert saved["last_classified_appointment_id"] == "a1"
    assert saved["month_before_sent_at"] is None
    assert fake_client.store["return_reminders"][0]["patient_id"] == "p1"


async def test_save_classification_atualiza_linha_existente(fake_client):
    fake_client.store["return_reminders"] = [{
        "patient_id": "p1", "doctor_id": JULIO_ID, "return_interval": "1_mes",
        "next_return_date": "2026-06-01", "last_classified_appointment_id": "a0",
        "month_before_sent_at": "2026-05-01T00:00:00+00:00",
        "month_of_sent_at": None, "overdue_sent_at": None,
    }]
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="6_meses",
    )
    assert len(fake_client.store["return_reminders"]) == 1  # não duplica linha
    assert saved["return_interval"] == "6_meses"
    assert saved["last_classified_appointment_id"] == "a1"
    # novo ciclo -> flags zeradas
    assert saved["month_before_sent_at"] is None


async def test_save_classification_15_dias_marca_as_3_flags_como_enviadas(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="15_dias",
    )
    assert saved["month_before_sent_at"] is not None
    assert saved["month_of_sent_at"] is not None
    assert saved["overdue_sent_at"] is not None


async def test_save_classification_1_mes_marca_so_month_before_como_enviada(fake_client):
    saved = await rr.save_classification(
        fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
        appointment_date=date(2026, 7, 13), return_interval="1_mes",
    )
    assert saved["month_before_sent_at"] is not None
    assert saved["month_of_sent_at"] is None
    assert saved["overdue_sent_at"] is None


async def test_save_classification_interval_invalido_levanta_erro(fake_client):
    import pytest
    with pytest.raises(ValueError):
        await rr.save_classification(
            fake_client, patient_id="p1", doctor_id=JULIO_ID, appointment_id="a1",
            appointment_date=date(2026, 7, 13), return_interval="2_meses",
        )
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: FAIL com `AttributeError: module 'return_reminders' has no attribute 'save_classification'`

- [ ] **Step 3: Implementar `save_classification`**

Acrescente ao final de `dashboard/return_reminders.py`:

```python
async def save_classification(
    client,
    patient_id: str,
    doctor_id: str,
    appointment_id: str,
    appointment_date: date,
    return_interval: str,
) -> dict:
    """Grava/atualiza a classificação de retorno de um paciente (1 linha por paciente).

    Zera as flags de envio (novo ciclo). Casos especiais: `15_dias` já marca
    as 3 flags como enviadas (intervalo curto demais para os lembretes
    mensais fazerem sentido); `1_mes` já marca `month_before_sent_at` (o
    mês-alvo é sempre o mês seguinte à própria classificação, então "um mês
    antes" sairia colado na consulta que acabou de acontecer).
    """
    if return_interval not in RETURN_INTERVALS:
        raise ValueError(f"return_interval inválido: {return_interval!r}")

    next_return_date = compute_next_return_date(appointment_date, return_interval)
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "return_interval": return_interval,
        "next_return_date": next_return_date.isoformat(),
        "last_classified_appointment_id": appointment_id,
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
        "updated_at": now,
    }
    if return_interval == "15_dias":
        payload["month_before_sent_at"] = now
        payload["month_of_sent_at"] = now
        payload["overdue_sent_at"] = now
    elif return_interval == "1_mes":
        payload["month_before_sent_at"] = now

    existing = await (
        client.from_("return_reminders").select("id").eq("patient_id", patient_id).execute()
    )
    if existing.data:
        result = await (
            client.from_("return_reminders").update(payload).eq("patient_id", patient_id).execute()
        )
    else:
        result = await client.from_("return_reminders").insert(payload).execute()
    return (result.data or [payload])[0]
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd dashboard && uv run pytest tests/test_return_reminders.py -v`
Expected: PASS (17 testes no total)

- [ ] **Step 5: Commit**

```bash
git add dashboard/return_reminders.py dashboard/tests/test_return_reminders.py
git commit -m "feat(dashboard): salva classificação de retorno com casos especiais de 15_dias/1_mes"
```

---

## Task 5: Rotas `/retornos` e `/api/retornos/{patient_id}` em `dashboard/main.py`

**Files:**
- Modify: `dashboard/main.py`
- Test: `dashboard/tests/test_main_retornos.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
# dashboard/tests/test_main_retornos.py
from starlette.testclient import TestClient

import main as dashboard_main
import return_reminders

AUTH = ("user", "changeme")
JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"


def _client():
    return TestClient(dashboard_main.app)


def test_retornos_page_requires_auth():
    r = _client().get("/retornos")
    assert r.status_code == 401


def test_retornos_page_ok(monkeypatch):
    async def fake_today(client, doctor_id, today=None):
        return [{"appointment_id": "a1", "patient_id": "p1", "start_time": "2026-07-13T12:00:00+00:00",
                 "patients": {"name": "João"}}]

    async def fake_pending(client, doctor_id):
        return []

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(return_reminders, "get_today_appointments", fake_today)
    monkeypatch.setattr(return_reminders, "get_pending_classification", fake_pending)

    r = _client().get("/retornos", auth=AUTH, params={"medico": "julio"})
    assert r.status_code == 200
    assert "João" in r.text


def test_api_salvar_retorno_requires_auth():
    r = _client().post(
        "/api/retornos/p1",
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "3_meses"},
    )
    assert r.status_code == 401


def test_api_salvar_retorno_ok(monkeypatch):
    calls = {}

    async def fake_save(client, patient_id, doctor_id, appointment_id, appointment_date, return_interval):
        calls["args"] = (patient_id, doctor_id, appointment_id, appointment_date, return_interval)
        return {"patient_id": patient_id, "return_interval": return_interval}

    monkeypatch.setattr(dashboard_main, "get_supabase", lambda: object())
    monkeypatch.setattr(return_reminders, "save_classification", fake_save)

    r = _client().post(
        "/api/retornos/p1",
        auth=AUTH,
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "3_meses"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    from datetime import date
    assert calls["args"] == ("p1", JULIO_ID, "a1", date(2026, 7, 13), "3_meses")


def test_api_salvar_retorno_interval_invalido_retorna_400():
    r = _client().post(
        "/api/retornos/p1",
        auth=AUTH,
        json={"doctor_id": JULIO_ID, "appointment_id": "a1",
              "appointment_date": "2026-07-13", "return_interval": "2_meses"},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -v`
Expected: FAIL com 404 (rota `/retornos` não existe ainda)

- [ ] **Step 3: Implementar as rotas**

Em `dashboard/main.py`, adicione o import perto dos outros (`import attendant_routes` / `import payments`):

```python
import return_reminders
```

E adicione, depois da rota `pagamentos_page` (por volta da linha 228):

```python
from datetime import date as _date


class RetornoBody(BaseModel):
    doctor_id: str
    appointment_id: str
    appointment_date: str  # 'YYYY-MM-DD'
    return_interval: str


@app.get("/retornos")
async def retornos_page(request: Request, medico: str = "julio", username: str = Depends(verify_credentials)):
    client = get_supabase()
    doctor_id = return_reminders.DOCTOR_ID_BY_KEY.get(medico, return_reminders.DOCTOR_ID_BY_KEY["julio"])
    hoje = await return_reminders.get_today_appointments(client, doctor_id)
    pendentes = await return_reminders.get_pending_classification(client, doctor_id)
    return templates.TemplateResponse(request, "retornos.html", {
        "username": username,
        "medico": medico,
        "hoje": hoje,
        "pendentes": pendentes,
        "intervalos": return_reminders.RETURN_INTERVAL_LABELS,
        "medico_doctor_id": doctor_id,
    })


@app.post("/api/retornos/{patient_id}")
async def api_salvar_retorno(patient_id: str, body: RetornoBody, username: str = Depends(verify_credentials)):
    if body.return_interval not in return_reminders.RETURN_INTERVALS:
        raise HTTPException(status_code=400, detail="return_interval inválido")
    client = get_supabase()
    appointment_date = _date.fromisoformat(body.appointment_date)
    saved = await return_reminders.save_classification(
        client, patient_id, body.doctor_id, body.appointment_id, appointment_date, body.return_interval,
    )
    return {"ok": True, "return_reminder": saved}
```

Isso referencia o template `retornos.html`, que ainda não existe — criado na
Task 6. Para este passo, crie um placeholder mínimo só pra rota não quebrar
com `TemplateNotFound`:

```html
{# dashboard/templates/retornos.html — placeholder, substituído na Task 6 #}
{% extends "base.html" %}
{% block content %}
<div>retornos</div>
{% endblock %}
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add dashboard/main.py dashboard/templates/retornos.html dashboard/tests/test_main_retornos.py
git commit -m "feat(dashboard): rotas GET /retornos e POST /api/retornos/{patient_id}"
```

---

## Task 6: UI de `dashboard/templates/retornos.html`

**Files:**
- Modify: `dashboard/templates/retornos.html`

Sem teste automatizado (é HTML/JS de UI) — verificação manual no fim da task.

- [ ] **Step 1: Escrever o template completo**

Substitua o conteúdo placeholder de `dashboard/templates/retornos.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="min-h-screen bg-slate-50 overflow-y-auto">
  <div class="max-w-5xl mx-auto px-6 py-8">

    <div class="flex items-center justify-between mb-6 flex-wrap gap-3">
      <div>
        <h1 class="text-xl font-bold text-slate-800">Classificação de Retorno</h1>
        <p class="text-sm text-slate-500">Defina de quanto em quanto tempo cada paciente deve retornar.</p>
      </div>
      <a href="/" class="text-sm text-cyan-700 hover:text-cyan-900 font-medium">&larr; Conversas</a>
    </div>

    <!-- Abas por médico -->
    <div class="flex gap-2 mb-6">
      <a href="/retornos?medico=julio"
         class="px-4 py-2 rounded-lg text-sm font-semibold {{ 'bg-cyan-600 text-white' if medico == 'julio' else 'bg-white text-slate-600 border border-slate-200' }}">
        Dr. Júlio
      </a>
      <a href="/retornos?medico=bruna"
         class="px-4 py-2 rounded-lg text-sm font-semibold {{ 'bg-cyan-600 text-white' if medico == 'bruna' else 'bg-white text-slate-600 border border-slate-200' }}">
        Dra. Bruna
      </a>
    </div>

    <!-- Seção Hoje -->
    <h2 class="text-sm font-bold uppercase tracking-wide text-slate-500 mb-2">Hoje</h2>
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm mb-8 overflow-hidden">
      {% if not hoje %}
      <div class="p-6 text-sm text-slate-400">Nenhuma consulta hoje.</div>
      {% else %}
      <table class="w-full text-sm" id="tabela-hoje">
        <thead class="bg-slate-50 text-slate-500 uppercase text-xs">
          <tr><th class="text-left px-4 py-2">Paciente</th><th class="text-left px-4 py-2">Horário</th>
              <th class="text-left px-4 py-2">Intervalo de retorno</th><th class="px-4 py-2"></th></tr>
        </thead>
        <tbody>
          {% for a in hoje %}
          <tr id="row-{{ a.appointment_id }}"
              data-patient-id="{{ a.patient_id }}"
              data-appointment-id="{{ a.appointment_id }}"
              data-appointment-date="{{ a.start_time[:10] }}"
              class="border-t border-slate-100">
            <td class="px-4 py-2 font-medium text-slate-700">{{ a.patients.name if a.patients else "Paciente" }}</td>
            <td class="px-4 py-2 text-slate-500 font-mono text-xs">{{ a.start_time[11:16] }}</td>
            <td class="px-4 py-2">
              <select class="intervalo-select border border-slate-200 rounded-md px-2 py-1 text-sm">
                <option value="">Nenhum</option>
                {% for value, label in intervalos.items() %}
                <option value="{{ value }}">{{ label }}</option>
                {% endfor %}
              </select>
            </td>
            <td class="px-4 py-2 text-right">
              <button type="button" class="salvar-btn bg-cyan-600 hover:bg-cyan-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md"
                      onclick="salvarRetorno('{{ medico_doctor_id }}', this)">Salvar</button>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% endif %}
    </div>

    <!-- Seção Pendentes -->
    <h2 class="text-sm font-bold uppercase tracking-wide text-slate-500 mb-2">Pendentes de classificação</h2>
    <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      {% if not pendentes %}
      <div class="p-6 text-sm text-slate-400">Nenhuma pendência — tudo classificado.</div>
      {% else %}
      <table class="w-full text-sm" id="tabela-pendentes">
        <thead class="bg-slate-50 text-slate-500 uppercase text-xs">
          <tr><th class="text-left px-4 py-2">Paciente</th><th class="text-left px-4 py-2">Consulta</th>
              <th class="text-left px-4 py-2">Intervalo de retorno</th><th class="px-4 py-2"></th></tr>
        </thead>
        <tbody>
          {% for a in pendentes %}
          <tr id="row-{{ a.appointment_id }}"
              data-patient-id="{{ a.patient_id }}"
              data-appointment-id="{{ a.appointment_id }}"
              data-appointment-date="{{ a.start_time[:10] }}"
              class="border-t border-slate-100">
            <td class="px-4 py-2 font-medium text-slate-700">{{ a.patients.name if a.patients else "Paciente" }}</td>
            <td class="px-4 py-2 text-slate-500 font-mono text-xs">{{ a.start_time[:10] }}</td>
            <td class="px-4 py-2">
              <select class="intervalo-select border border-slate-200 rounded-md px-2 py-1 text-sm">
                <option value="">Nenhum</option>
                {% for value, label in intervalos.items() %}
                <option value="{{ value }}">{{ label }}</option>
                {% endfor %}
              </select>
            </td>
            <td class="px-4 py-2 text-right">
              <button type="button" class="salvar-btn bg-cyan-600 hover:bg-cyan-700 text-white text-xs font-semibold px-3 py-1.5 rounded-md"
                      onclick="salvarRetorno('{{ medico_doctor_id }}', this)">Salvar</button>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      {% endif %}
    </div>

  </div>
</div>

<script>
async function salvarRetorno(doctorId, btn) {
  const row = btn.closest('tr');
  const select = row.querySelector('.intervalo-select');
  const returnInterval = select.value;
  if (!returnInterval) {
    alert('Escolha um intervalo de retorno.');
    return;
  }
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'Salvando…';
  try {
    const resp = await fetch(`/api/retornos/${row.dataset.patientId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doctor_id: doctorId,
        appointment_id: row.dataset.appointmentId,
        appointment_date: row.dataset.appointmentDate,
        return_interval: returnInterval,
      }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    row.style.opacity = '0';
    setTimeout(() => row.remove(), 200);
  } catch (err) {
    console.error(err);
    alert('Erro ao salvar. Tente novamente.');
    btn.disabled = false;
    btn.textContent = original;
  }
}
</script>
{% endblock %}
```

O botão usa `{{ medico_doctor_id }}`, que já está no contexto do template
desde a Task 5 (`retornos_page` em `dashboard/main.py`) — nenhuma mudança
adicional necessária em `main.py` nesta task.

- [ ] **Step 2: Rodar os testes existentes pra garantir que não quebrou nada**

Run: `cd dashboard && uv run pytest tests/test_main_retornos.py -v`
Expected: PASS (5 testes, mesmo do Task 5 — a rota agora renderiza o template de verdade)

- [ ] **Step 3: Verificação manual**

Suba o dashboard localmente (`cd dashboard && uv run uvicorn main:app --reload --port 8001`,
com `SUPABASE_URL`/`SUPABASE_KEY`/`DASHBOARD_PASSWORD` no `.env`) e abra
`http://localhost:8001/retornos?medico=julio` no navegador. Confirme:
- As abas Dr. Júlio / Dra. Bruna alternam a lista.
- Um paciente com consulta hoje aparece em "Hoje".
- Ao escolher um intervalo e clicar "Salvar", a linha some (sucesso) ou
  mostra alerta de erro.
- Volte no Supabase e confirme que a linha foi criada/atualizada em
  `return_reminders` com `next_return_date` correto.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/retornos.html dashboard/main.py
git commit -m "feat(dashboard): UI da página /retornos"
```

---

## Task 7: `scripts/send_return_reminders.py` — seleção do template por mês

**Files:**
- Create: `scripts/send_return_reminders.py`
- Test: `tests/test_return_reminders_cron.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_return_reminders_cron.py
from datetime import date

import scripts.send_return_reminders as srr

JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"


def _row(**overrides):
    row = {
        "id": "rr1",
        "patient_id": "p1",
        "doctor_id": JULIO_ID,
        "return_interval": "3_meses",
        "next_return_date": "2026-10-13",
        "month_before_sent_at": None,
        "month_of_sent_at": None,
        "overdue_sent_at": None,
        "patients": {"name": "João"},
    }
    row.update(overrides)
    return row


def test_pending_template_um_mes_antes():
    result = srr.pending_template(date(2026, 9, 15), _row())
    assert result == ("retorno_um_mes_antes", "month_before_sent_at")


def test_pending_template_no_mes():
    result = srr.pending_template(date(2026, 10, 20), _row())
    assert result == ("retorno_no_mes", "month_of_sent_at")


def test_pending_template_atrasado():
    result = srr.pending_template(date(2026, 11, 1), _row())
    assert result == ("retorno_atrasado", "overdue_sent_at")


def test_pending_template_nada_a_enviar_fora_das_janelas():
    result = srr.pending_template(date(2026, 8, 1), _row())
    assert result is None


def test_pending_template_ja_enviado_nao_repete():
    row = _row(month_before_sent_at="2026-09-01T00:00:00+00:00")
    result = srr.pending_template(date(2026, 9, 15), row)
    assert result is None


def test_pending_template_15_dias_nunca_dispara():
    row = _row(return_interval="15_dias", next_return_date="2026-07-20")
    result = srr.pending_template(date(2026, 7, 20), row)
    assert result is None


def test_pending_template_1_mes_pula_um_mes_antes_via_flag():
    # save_classification já marca month_before_sent_at pra 1_mes no momento
    # da classificação — o cron não precisa de lógica especial, só respeita a flag.
    row = _row(return_interval="1_mes", next_return_date="2026-08-13",
               month_before_sent_at="2026-07-13T00:00:00+00:00")
    result = srr.pending_template(date(2026, 7, 14), row)
    assert result is None  # mês-antes já marcado, e ainda não é agosto (mês do retorno)


def test_pending_template_virada_de_ano_nao_quebra():
    # dezembro/2026 é o mês antes de janeiro/2027 -> não pode comparar só o
    # número do mês (12 != 1 - 1), tem que normalizar por (ano, mês).
    row = _row(next_return_date="2027-01-10")
    result = srr.pending_template(date(2026, 12, 5), row)
    assert result == ("retorno_um_mes_antes", "month_before_sent_at")
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.send_return_reminders'`

- [ ] **Step 3: Implementar `pending_template` e o cabeçalho do script**

```python
# scripts/send_return_reminders.py
"""
Send WhatsApp return reminders (retorno periódico) via Meta Cloud API templates.
Runs once a day via GitHub Actions.

Reminders are driven by `return_reminders` (populated by the /retornos
dashboard, where the doctor sets a return_interval per patient after seeing
them). next_return_date is compared to today by CALENDAR MONTH, not exact
days:
  - month before next_return_date's month -> retorno_um_mes_antes
  - same month as next_return_date        -> retorno_no_mes
  - after next_return_date's month        -> retorno_atrasado (envio único)

`15_dias` rows never fire (all 3 flags pre-marked at classification time —
see dashboard/return_reminders.py::save_classification). `1_mes` rows never
fire retorno_um_mes_antes for the same reason (that flag is also pre-marked).

Sends are throttled — batches of 10, 60s pause between batches — to reduce
risk of the WhatsApp number being flagged for spam. Only the flag for a
successfully-sent reminder is marked, so anything left over from today's run
is retried automatically on tomorrow's run.

Requires in Supabase: tabela `return_reminders` (ver
supabase/migrations/20260714_create_return_reminders.sql).
"""
import asyncio
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient

TZ = ZoneInfo("America/Recife")
BATCH_SIZE = 10
BATCH_PAUSE_SECONDS = 60

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def _month_key(d: date) -> int:
    return d.year * 12 + d.month


def pending_template(today: date, row: dict) -> tuple[str, str] | None:
    """Retorna (template_name, coluna_de_flag) a disparar hoje para esta linha, ou None."""
    if row.get("return_interval") == "15_dias":
        return None

    next_return_date = date.fromisoformat(row["next_return_date"])
    current_key = _month_key(today)
    target_key = _month_key(next_return_date)

    if row.get("month_before_sent_at") is None and current_key == target_key - 1:
        return ("retorno_um_mes_antes", "month_before_sent_at")
    if row.get("month_of_sent_at") is None and current_key == target_key:
        return ("retorno_no_mes", "month_of_sent_at")
    if row.get("overdue_sent_at") is None and current_key > target_key:
        return ("retorno_atrasado", "overdue_sent_at")
    return None
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: PASS (8 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders_cron.py
git commit -m "feat(cron): seleciona template de retorno por mês calendário"
```

---

## Task 8: `scripts/send_return_reminders.py` — skip por consulta futura e envio por paciente

**Files:**
- Modify: `scripts/send_return_reminders.py`
- Test: `tests/test_return_reminders_cron.py`

- [ ] **Step 1: Escrever os testes que falham**

```python
# tests/test_return_reminders_cron.py (acrescentar ao final)
from unittest.mock import AsyncMock, MagicMock, patch


def _client_returning(data):
    execute = AsyncMock(return_value=MagicMock(data=data))
    table = MagicMock()
    for m in ("select", "eq", "gt", "limit", "update"):
        getattr(table, m).return_value = table
    table.execute = execute
    client = MagicMock()
    client.from_.return_value = table
    return client, table


# ── _has_future_appointment ───────────────────────────────────────────────


async def test_has_future_appointment_true_quando_existe():
    client, _ = _client_returning([{"id": "a9"}])
    out = await srr._has_future_appointment(client, "p1", JULIO_ID, "2026-07-13T00:00:00+00:00")
    assert out is True


async def test_has_future_appointment_false_quando_vazio():
    client, _ = _client_returning([])
    out = await srr._has_future_appointment(client, "p1", JULIO_ID, "2026-07-13T00:00:00+00:00")
    assert out is False


# ── _send_for_row ─────────────────────────────────────────────────────────


async def test_send_for_row_envia_a_todos_contatos_consulta_e_marca_flag():
    client, table = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}, {"phone": "5581222", "name": "Mãe"}]
    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    assert mock_send.await_count == 2
    table.update.assert_called_once()


async def test_send_for_row_sem_contato_nao_envia_nem_marca():
    client, table = _client_returning([])
    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=[]), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               new_callable=AsyncMock) as mock_send:
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    mock_send.assert_not_awaited()
    table.update.assert_not_called()


async def test_send_for_row_marca_flag_mesmo_se_um_contato_falhar():
    client, table = _client_returning([])
    contacts = [{"phone": "5581111", "name": "João"}, {"phone": "5581222", "name": "Mãe"}]

    async def flaky(phone, *a, **k):
        if phone == "5581111":
            raise RuntimeError("falha transitória")

    with patch("scripts.send_return_reminders.get_contacts_for_patient",
               new_callable=AsyncMock, return_value=contacts), \
         patch("scripts.send_return_reminders.send_return_reminder_template",
               side_effect=flaky):
        await srr._send_for_row(client, _row(), "retorno_no_mes", "month_of_sent_at", None)
    table.update.assert_called_once()
```

Adicione o import no topo do arquivo de teste:

```python
import pytest
pytestmark = pytest.mark.asyncio
```

(se `pytest.ini`/`pyproject.toml` da raiz já tiver `asyncio_mode = auto`,
confirme antes — se sim, essas duas linhas não são necessárias; siga o
padrão de `tests/test_reminders.py`, que usa `@pytest.mark.asyncio` por
função.)

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: FAIL com `AttributeError: module 'scripts.send_return_reminders' has no attribute '_has_future_appointment'`

- [ ] **Step 3: Implementar**

Acrescente ao final de `scripts/send_return_reminders.py`:

```python
def _plain_message(template_name: str, first_name: str, doctor_label: str) -> str:
    if template_name == "retorno_um_mes_antes":
        return (
            f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Passando para avisar que "
            f"seu retorno com {doctor_label} está previsto para o mês que vem.\n\n"
            f"Manter a regularidade das consultas é fundamental para o acompanhamento do seu "
            f"tratamento, especialmente considerando que a renovação de receitas de medicamentos "
            f"controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código "
            f"de Ética Médica. Assim você evita ficar sem acesso à medicação quando chegar a hora."
            f"\n\nSe quiser já deixar reservado um horário, é só nos avisar por aqui!"
        )
    if template_name == "retorno_no_mes":
        return (
            f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Verificamos que você está "
            f"no período indicado para a sua próxima consulta com {doctor_label}, gostaria de "
            f"agendar?\n\nManter a regularidade das consultas é fundamental para o acompanhamento "
            f"do seu tratamento. Além disso, a renovação de receitas de medicamentos controlados "
            f"depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então "
            f"agendar em dia é importante para que você não fique sem acesso à medicação."
            f"\n\nEstamos à disposição para agendar o horário que melhor se encaixa para você!"
        )
    return (
        f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Notamos que o período indicado "
        f"para o seu retorno com {doctor_label} já passou. Como o acompanhamento regular é "
        f"importante para a continuidade do seu tratamento, ficamos à disposição para remarcar o "
        f"quanto antes.\n\nVale lembrar também que a renovação de receitas de medicamentos "
        f"controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), "
        f"então quanto antes retomarmos as consultas, menor o risco de você ficar sem acesso à "
        f"medicação.\n\nSe puder nos responder com sua disponibilidade, já organizamos um horário "
        f"para você."
    )


async def send_return_reminder_template(phone: str, template_name: str, first_name: str, doctor_label: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    plain = _plain_message(template_name, first_name, doctor_label)
    await send_template_message(
        conv_id,
        template_name=template_name,
        language="pt_BR",
        category="UTILITY",
        body_params={"1": first_name, "2": doctor_label},
        content=plain,
    )


async def save_to_checkpoint(graph, phone: str, message: str, patient_name: str, doctor_key: str) -> None:
    from langchain_core.messages import AIMessage
    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}
    snapshot = await graph.aget_state(config)
    update: dict = {"messages": [AIMessage(content=message)]}
    if not snapshot.values:
        update.update({
            "phone": thread_phone,
            "stage": "patient_agent",
            "user_name": patient_name,
            "patient_name": patient_name,
            "is_patient": True,
            "preferred_doctor": doctor_key,
        })
    await graph.aupdate_state(config, update, as_node="patient_agent")


async def _has_future_appointment(client, patient_id: str, doctor_id: str, now_iso: str) -> bool:
    result = await (
        client.from_("appointments")
        .select("id")
        .eq("patient_id", patient_id)
        .eq("doctor_id", doctor_id)
        .eq("status", "scheduled")
        .gt("end_time", now_iso)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def _send_for_row(client, row: dict, template_name: str, sent_col: str, graph) -> None:
    patient_id = row.get("patient_id")
    patient = row.get("patients") or {}
    patient_name = patient.get("name") or "paciente"
    from app.utils import display_name as _dn
    doctor_label = DOCTOR_LABELS.get(row.get("doctor_id", ""), "médico(a)")
    doctor_key = DOCTOR_KEYS.get(row.get("doctor_id", ""), "")

    contacts = await get_contacts_for_patient(patient_id, "consulta") if patient_id else []
    if not contacts:
        print(f"  [SKIP] return_reminder {row.get('id')} sem contato de consulta (patient_id={patient_id})")
        return

    sent_any = False
    for contact in contacts:
        phone = contact.get("phone")
        if not phone:
            continue
        first_name = _dn(contact.get("name") or patient_name)
        try:
            await send_return_reminder_template(phone, template_name, first_name, doctor_label)
            message = _plain_message(template_name, first_name, doctor_label)
            if graph:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            print(f"  [{template_name}] Sent to {phone} — {patient_name}")
            sent_any = True
        except Exception as e:
            print(f"  Failed to send to {phone}: {e}")

    if sent_any:
        await client.from_("return_reminders").update({
            sent_col: datetime.now(timezone.utc).isoformat(),
        }).eq("id", row["id"]).execute()
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: PASS (15 testes no total)

- [ ] **Step 5: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders_cron.py
git commit -m "feat(cron): envia lembrete de retorno aos contatos e marca a flag correspondente"
```

---

## Task 9: `main()` com batching + workflow do GitHub Actions

**Files:**
- Modify: `scripts/send_return_reminders.py`
- Create: `.github/workflows/return_reminders.yml`
- Test: `tests/test_return_reminders_cron.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_return_reminders_cron.py (acrescentar ao final)


async def test_main_pula_linha_com_consulta_futura_do_mesmo_medico():
    # next_return_date no mesmo mês de "hoje" (mockado) -> pending_template
    # garantidamente dá match em retorno_no_mes; o único motivo pra
    # _send_for_row não ser chamado deve ser o skip por consulta futura.
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=[_row(next_return_date="2026-09-13")]))

    appt_table = MagicMock()
    for m in ("select", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[{"id": "a-future"}]))

    def from_(table_name):
        return rr_table if table_name == "return_reminders" else appt_table

    client = MagicMock()
    client.from_.side_effect = from_

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_return_reminders._send_for_row",
               new_callable=AsyncMock) as mock_send, \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=srr.TZ)
            await srr.main()

    mock_send.assert_not_awaited()


async def test_main_processa_em_lotes_com_pausa():
    rows = [_row(id=f"rr{i}", next_return_date="2026-10-13") for i in range(12)]
    rr_table = MagicMock()
    for m in ("select", "neq"):
        getattr(rr_table, m).return_value = rr_table
    rr_table.execute = AsyncMock(return_value=MagicMock(data=rows))

    appt_table = MagicMock()
    for m in ("select", "eq", "gt", "limit"):
        getattr(appt_table, m).return_value = appt_table
    appt_table.execute = AsyncMock(return_value=MagicMock(data=[]))  # sem consulta futura

    def from_(table_name):
        return rr_table if table_name == "return_reminders" else appt_table

    client = MagicMock()
    client.from_.side_effect = from_

    with patch("supabase.acreate_client", new_callable=AsyncMock, return_value=client), \
         patch("scripts.send_return_reminders._send_for_row",
               new_callable=AsyncMock) as mock_send, \
         patch("scripts.send_return_reminders.asyncio.sleep",
               new_callable=AsyncMock) as mock_sleep, \
         patch.dict(os.environ, {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}, clear=False):
        os.environ.pop("SUPABASE_CONNECTION_STRING", None)
        # força "hoje" pra dentro da janela retorno_um_mes_antes (12 candidatos).
        # Só `.now` é sobrescrito — `.fromisoformat` continua a implementação
        # real (usada por pending_template via `date.fromisoformat`, que não é
        # afetado por este patch pois é um símbolo separado).
        with patch("scripts.send_return_reminders.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 15, tzinfo=srr.TZ)
            await srr.main()

    assert mock_send.await_count == 12  # 12 linhas, todas elegíveis (nada enviado ainda)
    mock_sleep.assert_awaited_once_with(srr.BATCH_PAUSE_SECONDS)  # 2 lotes de 10 -> 1 pausa
```

Adicione os imports que faltam no topo do arquivo de teste:

```python
from datetime import datetime
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: FAIL com `AttributeError: module 'scripts.send_return_reminders' has no attribute 'main'`

- [ ] **Step 3: Implementar `main()`**

Acrescente ao final de `scripts/send_return_reminders.py`:

```python
async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    today = datetime.now(TZ).date()
    now_iso = datetime.now(timezone.utc).isoformat()

    result = await (
        client.from_("return_reminders")
        .select(
            "id, patient_id, doctor_id, return_interval, next_return_date, "
            "month_before_sent_at, month_of_sent_at, overdue_sent_at, patients(name)"
        )
        .neq("return_interval", "15_dias")
        .execute()
    )
    rows = result.data or []

    candidates = []
    for row in rows:
        pending = pending_template(today, row)
        if not pending:
            continue
        patient_id = row.get("patient_id")
        doctor_id = row.get("doctor_id")
        if patient_id and doctor_id and await _has_future_appointment(client, patient_id, doctor_id, now_iso):
            print(f"  [SKIP] return_reminder {row.get('id')} — já tem consulta futura com o mesmo médico.")
            continue
        template_name, sent_col = pending
        candidates.append((row, template_name, sent_col))

    print(f"Return reminders to send today: {len(candidates)}")

    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        pg_conn = await AsyncConnection.connect(
            conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
        )
        checkpointer = AsyncPostgresSaver(pg_conn)
        graph = build_graph(checkpointer=checkpointer)
    else:
        print("SUPABASE_CONNECTION_STRING not set — reminders won't be saved to LangGraph checkpoint.")

    try:
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            for row, template_name, sent_col in batch:
                await _send_for_row(client, row, template_name, sent_col, graph)
            if i + BATCH_SIZE < len(candidates):
                await asyncio.sleep(BATCH_PAUSE_SECONDS)
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `uv run pytest tests/test_return_reminders_cron.py -v`
Expected: PASS (17 testes no total)

- [ ] **Step 5: Rodar a suíte inteira da raiz pra garantir que nada quebrou**

Run: `uv run pytest --tb=short`
Expected: PASS

- [ ] **Step 6: Criar o workflow do GitHub Actions**

```yaml
# .github/workflows/return_reminders.yml
name: Send return reminders

on:
  schedule:
    - cron: "0 11 * * *"  # ~8h Recife (UTC-3)
  workflow_dispatch:        # allow manual runs for testing

jobs:
  return-reminders:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: astral-sh/setup-uv@v5

      - name: Install dependencies
        run: uv sync --frozen --no-dev

      - name: Send return reminders
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          SUPABASE_CONNECTION_STRING: ${{ secrets.SUPABASE_CONNECTION_STRING }}
          WHATSAPP_TOKEN: ${{ secrets.WHATSAPP_TOKEN }}
          WHATSAPP_PHONE_NUMBER_ID: ${{ secrets.WHATSAPP_PHONE_NUMBER_ID }}
          CHATWOOT_BASE_URL: ${{ secrets.CHATWOOT_BASE_URL }}
          CHATWOOT_ACCOUNT_ID: ${{ secrets.CHATWOOT_ACCOUNT_ID }}
          CHATWOOT_AGENT_BOT_TOKEN: ${{ secrets.CHATWOOT_AGENT_BOT_TOKEN }}
          CHATWOOT_USER_TOKEN: ${{ secrets.CHATWOOT_USER_TOKEN }}
          CHATWOOT_INBOX_ID: ${{ secrets.CHATWOOT_INBOX_ID }}
        run: uv run python scripts/send_return_reminders.py
```

- [ ] **Step 7: Commit**

```bash
git add scripts/send_return_reminders.py tests/test_return_reminders_cron.py .github/workflows/return_reminders.yml
git commit -m "feat(cron): batching de envio de lembretes de retorno + workflow diário"
```

---

## Task 10: Documentar os 3 templates novos

**Files:**
- Modify: `docs/whatsapp-templates.md`

- [ ] **Step 1: Acrescentar a seção dos templates novos**

No final de `docs/whatsapp-templates.md`, depois da tabela de "Templates
existentes (lembretes de consulta)", adicione:

```markdown
---

## Templates de retorno periódico

Enviados por [`scripts/send_return_reminders.py`](../scripts/send_return_reminders.py),
1x/dia, conforme a classificação feita pelo médico no dashboard `/retornos`
(ver [`dashboard/return_reminders.py`](../dashboard/return_reminders.py)).
Variáveis do corpo: `{{1}}` = primeiro nome do contato, `{{2}}` = médico(a).

Categoria: **Utility (Utilidade)**. Idioma: `pt_BR`. Sem cabeçalho/rodapé/botões.

**Precisam ser criados e aprovados no Meta Business Manager antes do cron
conseguir enviá-los.**

### `retorno_um_mes_antes`

Disparado quando o mês atual é o mês anterior ao de `next_return_date`. Nunca
disparado para `return_interval = 15_dias` ou `1_mes` (ver
`dashboard/return_reminders.py::save_classification`).

```
Olá! Tudo bem? 😊

Aqui é a Eva, secretária da Psiquê. Passando para avisar que seu retorno com {{2}} está previsto para o mês que vem.

Manter a regularidade das consultas é fundamental para o acompanhamento do seu tratamento, especialmente considerando que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código de Ética Médica. Assim você evita ficar sem acesso à medicação quando chegar a hora.

Se quiser já deixar reservado um horário, é só nos avisar por aqui!
```

### `retorno_no_mes`

Disparado quando o mês atual é o mesmo mês de `next_return_date`.

```
Olá! Tudo bem? 😊

Aqui é a Eva, secretária da Psiquê. Verificamos que você está no período indicado para a sua próxima consulta com {{2}}, gostaria de agendar?

Manter a regularidade das consultas é fundamental para o acompanhamento do seu tratamento. Além disso, a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então agendar em dia é importante para que você não fique sem acesso à medicação.

Estamos à disposição para agendar o horário que melhor se encaixa para você!
```

### `retorno_atrasado`

Disparado uma única vez quando o mês atual é posterior ao mês de
`next_return_date` (sem repetição mensal).

```
Olá! Tudo bem? 😊

Aqui é a Eva, secretária da Psiquê. Notamos que o período indicado para o seu retorno com {{2}} já passou. Como o acompanhamento regular é importante para a continuidade do seu tratamento, ficamos à disposição para remarcar o quanto antes.

Vale lembrar também que a renovação de receitas de medicamentos controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então quanto antes retomarmos as consultas, menor o risco de você ficar sem acesso à medicação.

Se puder nos responder com sua disponibilidade, já organizamos um horário para você.
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/whatsapp-templates.md
git commit -m "docs(templates): documenta os 3 templates de lembrete de retorno periódico"
```

---

## Pós-implementação (fora do escopo do código)

Depois que todas as tasks acima passarem, ainda faltam ações manuais fora do
código, que não podem ser automatizadas por este plano:

1. **Criar e submeter os 3 templates no Meta Business Manager** (texto exato
   em `docs/whatsapp-templates.md`) e aguardar aprovação — sem isso, o cron
   vai falhar ao tentar enviar (`send_template_message` levanta erro HTTP).
2. **Aplicar a migration** (`supabase/migrations/20260714_create_return_reminders.sql`)
   no Supabase de produção (Task 1, Step 2 já cobre isso, mas reforçando aqui
   porque é fácil esquecer antes do deploy).
3. **Configurar o secret `SUPABASE_CONNECTION_STRING`** no GitHub (se ainda
   não existir — outros workflows já usam, então provavelmente já está lá).
