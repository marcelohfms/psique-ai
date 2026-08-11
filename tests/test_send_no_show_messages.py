import asyncio
from unittest.mock import AsyncMock

import scripts.send_no_show_messages as sns


def _fake_client(appointments):
    class _Q:
        def __init__(self, table, store):
            self.table, self.store, self._f = table, store, {}

        def select(self, *a, **k):
            return self

        def eq(self, c, v):
            self._f[c] = v
            return self

        def is_(self, c, v):
            self._f[c] = ("is", v)
            return self

        def lt(self, *a, **k):
            return self

        def update(self, payload):
            self._f["_update"] = payload
            return self

        async def execute(self):
            if self.table != "appointments":
                return type("R", (), {"data": []})()
            if "_update" in self._f:
                for a in self.store["appointments"]:
                    if a["id"] == self._f.get("id"):
                        a.update(self._f["_update"])
                return type("R", (), {"data": []})()
            rows = [a for a in self.store["appointments"]
                    if a["status"] == "no_show" and a.get("no_show_message_sent_at") is None]
            return type("R", (), {"data": rows})()

    class _C:
        def __init__(self):
            self.store = {"appointments": appointments}

        def from_(self, t):
            return _Q(t, self.store)

    return _C()


def test_envia_so_para_no_show_sem_flag(monkeypatch):
    appts = [
        {"id": "r1", "appointment_id": "a1", "patient_id": "p1",
         "status": "no_show", "no_show_message_sent_at": None,
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "Carlos Silva"}},
        {"id": "r2", "appointment_id": "a2", "patient_id": "p2",
         "status": "no_show", "no_show_message_sent_at": "2026-07-02T00:00:00+00:00",
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "Maria"}},
    ]
    client = _fake_client(appts)
    send = AsyncMock()
    monkeypatch.setattr(sns, "send_no_show_message", send)
    monkeypatch.setattr(sns, "get_contacts_for_patient",
                        AsyncMock(return_value=[{"phone": "5581999999999"}]))

    sent = asyncio.run(sns.process(client))

    assert sent == 1
    assert send.await_count == 1  # só p1
    send.assert_awaited_once_with("5581999999999", "Carlos")
    # flag marcada em a1, intocada em a2
    assert appts[0]["no_show_message_sent_at"] is not None
    assert appts[1]["no_show_message_sent_at"] == "2026-07-02T00:00:00+00:00"


def test_nao_marca_flag_sem_contato(monkeypatch):
    appts = [
        {"id": "r1", "appointment_id": "a1", "patient_id": "p1",
         "status": "no_show", "no_show_message_sent_at": None,
         "start_time": "2026-07-01T12:00:00+00:00", "patients": {"name": "João"}},
    ]
    client = _fake_client(appts)
    send = AsyncMock()
    monkeypatch.setattr(sns, "send_no_show_message", send)
    monkeypatch.setattr(sns, "get_contacts_for_patient", AsyncMock(return_value=[]))

    sent = asyncio.run(sns.process(client))

    assert sent == 0
    send.assert_not_awaited()
    # sem contato: não marca a flag, para retentar amanhã
    assert appts[0]["no_show_message_sent_at"] is None
