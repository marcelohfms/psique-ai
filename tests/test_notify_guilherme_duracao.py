import asyncio
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import scripts.notify_guilherme_duracao_16nov as nd

TZ = ZoneInfo("America/Recife")
PID = nd.EXPECTED_PATIENT_ID

# Dados fictícios só para os testes — não são o contato/paciente real.
FAKE_PHONE = "5581990000000"
FAKE_CONTACT = "Ana Teste"
FAKE_PATIENT = "Bruno Teste"


def _appt(**over):
    base = {
        "id": nd.APPT_DB_ID,
        "patient_id": PID,
        "contact_id": "c-1",
        "status": "scheduled",
        "start_time": "2026-11-16T17:00:00+00:00",  # 14:00 Recife
        "end_time": "2026-11-16T18:00:00+00:00",     # 15:00 Recife → 60 min
        "confirmed_at": "2026-11-15T10:00:00+00:00",
    }
    base.update(over)
    return base


# ── funções puras ─────────────────────────────────────────────────────────────

def test_duration_minutes():
    assert nd.duration_minutes("2026-11-16T17:00:00+00:00", "2026-11-16T18:00:00+00:00") == 60
    assert nd.duration_minutes("2026-11-16T17:00:00+00:00", "2026-11-16T19:00:00+00:00") == 120


def test_first_name():
    assert nd.first_name("Ana Maria Teste") == "Ana"
    assert nd.first_name("  ") == ""
    assert nd.first_name(None) == ""


def test_in_window():
    assert nd.in_window(datetime(2026, 11, 14, 8, 0, tzinfo=TZ))
    assert nd.in_window(datetime(2026, 11, 16, 23, 0, tzinfo=TZ))
    assert not nd.in_window(datetime(2026, 11, 13, 23, 0, tzinfo=TZ))
    assert not nd.in_window(datetime(2026, 11, 17, 0, 1, tzinfo=TZ))


def test_build_message_usa_os_nomes():
    msg = nd.build_message(FAKE_CONTACT, FAKE_PATIENT)
    assert f"Oi, {FAKE_CONTACT}!" in msg
    assert f"consulta do {FAKE_PATIENT}" in msg
    assert "1 hora" in msg


def test_should_notify_ok():
    ok, _ = nd.should_notify(_appt(), already_sent=False)
    assert ok


def test_should_notify_bloqueia():
    assert not nd.should_notify(None, False)[0]
    assert not nd.should_notify(_appt(), already_sent=True)[0]
    assert not nd.should_notify(_appt(status="canceled"), False)[0]
    assert not nd.should_notify(_appt(confirmed_at=None), False)[0]
    # 2h ainda → não envia (mensagem ficaria incoerente)
    assert not nd.should_notify(_appt(end_time="2026-11-16T19:00:00+00:00"), False)[0]
    # paciente diferente → não envia
    assert not nd.should_notify(_appt(patient_id="outro"), False)[0]


# ── process (com client falso e envio mockado) ─────────────────────────────────

def _fake_client(appt, marker_rows=None, contact=None, patient_name=FAKE_PATIENT):
    marker_rows = marker_rows or []

    class _Q:
        def __init__(self, table, store):
            self.table, self.store = table, store

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def limit(self, *a, **k):
            return self

        async def execute(self):
            data = {
                "appointments": [self.store["appt"]] if self.store["appt"] else [],
                "events": self.store["marker_rows"],
                "contacts": [self.store["contact"]] if self.store["contact"] else [],
                "patients": [{"name": self.store["patient_name"]}],
            }[self.table]
            return type("R", (), {"data": data})()

    class _C:
        def __init__(self):
            self.store = {"appt": appt, "marker_rows": marker_rows,
                          "contact": contact, "patient_name": patient_name}

        def from_(self, t):
            return _Q(t, self.store)

    return _C()


def test_process_fora_da_janela_nao_toca_no_banco(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(nd, "_send_notice", sent)
    client = _fake_client(_appt())
    n = asyncio.run(nd.process(client, now=datetime(2026, 11, 13, 10, 0, tzinfo=TZ)))
    assert n == 0
    sent.assert_not_awaited()


def test_process_confirmada_envia_uma_vez(monkeypatch):
    sent = AsyncMock(return_value=42)
    monkeypatch.setattr(nd, "_send_notice", sent)
    import app.database as db
    monkeypatch.setattr(db, "save_message", AsyncMock())
    monkeypatch.setattr(db, "log_event", AsyncMock())

    client = _fake_client(
        _appt(), marker_rows=[],
        contact={"phone": FAKE_PHONE, "name": FAKE_CONTACT},
    )
    n = asyncio.run(nd.process(client, now=datetime(2026, 11, 15, 11, 0, tzinfo=TZ)))
    assert n == 1
    sent.assert_awaited_once()
    # a mensagem enviada usa os 1os nomes resolvidos do banco
    _, content = sent.await_args.args
    assert "Oi, Ana!" in content and "consulta do Bruno" in content


def test_process_nao_reenvia_se_ja_avisado(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(nd, "_send_notice", sent)
    client = _fake_client(
        _appt(), marker_rows=[{"id": "ev1"}],
        contact={"phone": FAKE_PHONE, "name": FAKE_CONTACT},
    )
    n = asyncio.run(nd.process(client, now=datetime(2026, 11, 15, 11, 0, tzinfo=TZ)))
    assert n == 0
    sent.assert_not_awaited()


def test_process_nao_confirmada_nao_envia(monkeypatch):
    sent = AsyncMock()
    monkeypatch.setattr(nd, "_send_notice", sent)
    client = _fake_client(
        _appt(confirmed_at=None),
        contact={"phone": FAKE_PHONE, "name": FAKE_CONTACT},
    )
    n = asyncio.run(nd.process(client, now=datetime(2026, 11, 15, 11, 0, tzinfo=TZ)))
    assert n == 0
    sent.assert_not_awaited()
