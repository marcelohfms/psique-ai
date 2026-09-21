import asyncio
from unittest.mock import MagicMock

import calendar_client as cc


# ── build_no_show_summary (lógica pura do título) ───────────────────────────

def test_summary_prefixa_nao_compareceu():
    assert (
        cc.build_no_show_summary("Consulta — Maria [Presencial]")
        == "❌ [Não compareceu] Consulta — Maria [Presencial]"
    )


def test_summary_remove_check_de_presenca_confirmada():
    # "✅" (presença confirmada) é contraditório com falta: sai antes de marcar.
    assert (
        cc.build_no_show_summary("✅ Consulta — Maria [Online]")
        == "❌ [Não compareceu] Consulta — Maria [Online]"
    )


def test_summary_idempotente_nao_duplica_prefixo():
    ja = "❌ [Não compareceu] Consulta — Maria [Presencial]"
    assert cc.build_no_show_summary(ja) == ja


def test_summary_vazio():
    assert cc.build_no_show_summary("") == "❌ [Não compareceu]"


def test_summary_preserva_edicao_manual_da_clinica():
    assert (
        cc.build_no_show_summary("CONSULTA MARIA 21/09")
        == "❌ [Não compareceu] CONSULTA MARIA 21/09"
    )


# ── mark_event_no_show (patch de cor + título no Google Calendar) ────────────

def test_mark_event_no_show_pinta_vermelho_e_marca_titulo(monkeypatch):
    captured = {}

    events = MagicMock()
    events.get.return_value.execute.return_value = {"summary": "✅ Consulta — João [Presencial]"}

    def _patch(calendarId, eventId, body):
        captured["calendarId"] = calendarId
        captured["eventId"] = eventId
        captured["body"] = body
        return MagicMock(execute=MagicMock(return_value={}))

    events.patch.side_effect = _patch
    service = MagicMock()
    service.events.return_value = events

    monkeypatch.setattr(cc, "_credentials", lambda: object())
    monkeypatch.setattr(cc, "build", lambda *a, **k: service)

    asyncio.run(cc.mark_event_no_show("cal-1", "evt-1"))

    assert captured["calendarId"] == "cal-1"
    assert captured["eventId"] == "evt-1"
    assert captured["body"]["colorId"] == "11"  # Tomato (vermelho)
    assert captured["body"]["summary"] == "❌ [Não compareceu] Consulta — João [Presencial]"
