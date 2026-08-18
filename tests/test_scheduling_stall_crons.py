"""Testes de integração (mockados) dos crons de agendamento abandonado."""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import scripts.send_scheduling_stall_nudges as nud
import scripts.send_scheduling_stall_report as rep

TZ_NOW = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)


def _case(phone="5583111", hours_ago=6, **md):
    return {"phone": phone,
            "offered_at": TZ_NOW - timedelta(hours=hours_ago),
            "metadata": md or {"doctor": "julio"}}


# ── send_scheduling_stall_nudges._send_nudge ─────────────────────────────────

async def test_nudge_sent_for_active_patient_in_window():
    user = {"active": True, "name": "Maria Silva", "preferred_doctor": "julio"}
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock, return_value=user), \
         patch.object(nud, "_window_open", new_callable=AsyncMock, return_value=True), \
         patch.object(nud, "send_whatsapp", new_callable=AsyncMock) as send, \
         patch.object(nud, "mark_handled", new_callable=AsyncMock) as mark:
        await nud._send_nudge(MagicMock(), None, _case(), TZ_NOW)

    send.assert_awaited_once()
    assert "Maria" in send.call_args[0][1]
    mark.assert_awaited_once()
    assert mark.call_args[0][2] == nud.NUDGE_EVENT


async def test_no_nudge_for_inactive_patient():
    """eva-inativa/pausado nunca é cutucado — vai para o e-mail da clínica."""
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"active": False, "name": "João"}), \
         patch.object(nud, "_window_open", new_callable=AsyncMock, return_value=True), \
         patch.object(nud, "send_whatsapp", new_callable=AsyncMock) as send, \
         patch.object(nud, "mark_handled", new_callable=AsyncMock) as mark:
        await nud._send_nudge(MagicMock(), None, _case(), TZ_NOW)

    send.assert_not_awaited()
    mark.assert_not_awaited()


async def test_no_nudge_for_cold_patient_outside_24h():
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"active": True, "name": "Ana"}), \
         patch.object(nud, "_window_open", new_callable=AsyncMock, return_value=False), \
         patch.object(nud, "send_whatsapp", new_callable=AsyncMock) as send, \
         patch.object(nud, "mark_handled", new_callable=AsyncMock) as mark:
        await nud._send_nudge(MagicMock(), None, _case(), TZ_NOW)

    send.assert_not_awaited()
    mark.assert_not_awaited()


async def test_nudge_send_failure_is_not_marked():
    """Se o envio falha, não marca — o próximo run tenta de novo."""
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"active": True, "name": "Ana"}), \
         patch.object(nud, "_window_open", new_callable=AsyncMock, return_value=True), \
         patch.object(nud, "send_whatsapp", new_callable=AsyncMock,
                      side_effect=RuntimeError("boom")), \
         patch.object(nud, "mark_handled", new_callable=AsyncMock) as mark:
        await nud._send_nudge(MagicMock(), None, _case(), TZ_NOW)

    mark.assert_not_awaited()


async def test_nudge_main_skips_outside_send_window():
    """Fora de 8h–20h, o cron encerra sem consultar nada."""
    late = datetime(2026, 8, 18, 22, 0, tzinfo=nud.TZ)
    with patch.object(nud, "datetime") as mdt, \
         patch.object(nud, "fetch_abandoned", new_callable=AsyncMock) as fetch:
        mdt.now.return_value = late
        await nud.main()
    fetch.assert_not_called()


# ── send_scheduling_stall_report.main ────────────────────────────────────────

_SMTP_ENV = {
    "SUPABASE_URL": "http://x", "SUPABASE_KEY": "k",
    "SMTP_HOST": "h", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
    "CLINIC_NOTIFY_EMAIL": "clinic@x.com",
}


async def test_report_excludes_nudge_eligible_and_marks_reported():
    """O e-mail lista pausados e frios, mas NÃO os ativos-em-janela (esses o nudge
    cutuca). Cada caso reportado é marcado como scheduling_stall_reported."""
    cases = [_case("55A"), _case("55B"), _case("55C")]
    users = {
        "55A": {"active": True, "name": "Ativo Janela"},    # elegível → excluído
        "55B": {"active": False, "name": "Pausado"},        # incluído
        "55C": {"active": True, "name": "Ativo Frio"},      # incluído (fora 24h)
    }
    windows = {"55A": True, "55C": False}

    async def _get_user(phone):
        return users[phone]

    async def _window(client, phone, now):
        return windows.get(phone, False)

    with patch.dict(os.environ, _SMTP_ENV, clear=False), \
         patch("supabase.acreate_client", new_callable=AsyncMock, return_value=MagicMock()), \
         patch.object(rep, "fetch_abandoned", new_callable=AsyncMock, return_value=cases), \
         patch("app.database.get_user_by_phone", side_effect=_get_user), \
         patch.object(rep, "_window_open_safe", side_effect=_window), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as email, \
         patch.object(rep, "mark_handled", new_callable=AsyncMock) as mark:
        await rep.main()

    email.assert_awaited_once()
    body = email.call_args[0][1]
    assert "Pausado" in body and "Ativo Frio" in body
    assert "Ativo Janela" not in body

    reported = {c.args[1] for c in mark.await_args_list}
    assert reported == {"55B", "55C"}
    assert all(c.args[2] == rep.REPORT_EVENT for c in mark.await_args_list)


async def test_report_no_cases_sends_no_email():
    with patch.dict(os.environ, _SMTP_ENV, clear=False), \
         patch("supabase.acreate_client", new_callable=AsyncMock, return_value=MagicMock()), \
         patch.object(rep, "fetch_abandoned", new_callable=AsyncMock, return_value=[]), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as email, \
         patch.object(rep, "mark_handled", new_callable=AsyncMock):
        await rep.main()

    email.assert_not_awaited()
