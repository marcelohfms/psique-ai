"""Tests for app/receipt_reconciliation.py — a rede de segurança que acha comprovante
que sumiu na ingestão (webhook da Meta que não chegou) e alerta a clínica."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 18, 22, 10, 0, tzinfo=timezone.utc)


def _cand(**over):
    base = {
        "attachment_id": 999,
        "phone": "5581971052827",
        "created_at": int(NOW.timestamp()) - 600,  # 10 min atrás, dentro da janela
        "data_url": "https://cw/a.jpg",
        "conversation_id": 505,
    }
    base.update(over)
    return base


def _patches(*, candidates, traced=False, alerted=False, kind="payment"):
    """Context managers para os quatro mocks externos do reconcile."""
    return (
        patch("app.receipt_reconciliation.list_recent_incoming_attachments",
              new_callable=AsyncMock, return_value=candidates),
        patch("app.receipt_reconciliation.has_trace",
              new_callable=AsyncMock, return_value=traced),
        patch("app.receipt_reconciliation.already_alerted",
              new_callable=AsyncMock, return_value=alerted),
        patch("app.receipt_reconciliation._download_bytes",
              new_callable=AsyncMock, return_value=b"img"),
        patch("app.receipt_reconciliation.classify_image_raw",
              new_callable=AsyncMock, return_value=(kind, "texto")),
        patch("app.receipt_reconciliation.send_clinic_notification_email",
              new_callable=AsyncMock),
        patch("app.receipt_reconciliation.log_event", new_callable=AsyncMock),
    )


async def test_untraced_payment_alerts_and_marks():
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand()], traced=False, alerted=False, kind="payment")
    with p[0], p[1], p[2], p[3], p[4], p[5] as mail, p[6] as logev:
        n = await reconcile(NOW)
    assert n == 1
    mail.assert_awaited_once()
    logev.assert_awaited_once()
    args = logev.await_args.args
    assert args[0] == "receipt_drop_alerted"
    assert args[1] == "5581971052827"
    assert args[2]["attachment_id"] == 999


async def test_traced_receipt_is_skipped():
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand()], traced=True)
    with p[0], p[1], p[2], p[3], p[4], p[5] as mail, p[6]:
        n = await reconcile(NOW)
    assert n == 0
    mail.assert_not_awaited()


async def test_ignore_image_not_alerted():
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand()], kind="ignore")
    with p[0], p[1], p[2], p[3], p[4], p[5] as mail, p[6] as logev:
        n = await reconcile(NOW)
    assert n == 0
    mail.assert_not_awaited()
    logev.assert_not_awaited()


async def test_already_alerted_skipped():
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand()], alerted=True)
    with p[0], p[1], p[2], p[3], p[4], p[5] as mail, p[6]:
        n = await reconcile(NOW)
    assert n == 0
    mail.assert_not_awaited()


async def test_floor_excludes_fresh_attachment():
    """A janela passada ao Chatwoot respeita o piso de 10 min e o teto de 60 min:
    anexo recém-chegado (ainda podendo estar em processamento) fica fora."""
    from app.receipt_reconciliation import reconcile
    with patch("app.receipt_reconciliation.list_recent_incoming_attachments",
               new_callable=AsyncMock, return_value=[]) as mock_list:
        await reconcile(NOW, floor_min=10, window_min=60)
    since_ts, until_ts = mock_list.await_args.args
    now_ts = int(NOW.timestamp())
    assert until_ts == now_ts - 10 * 60
    assert since_ts == now_ts - 60 * 60


async def test_phone_variant_matches_trace():
    """Comprovante registrado na forma de 13 dígitos (com o 9) tem que casar com o
    telefone de 12 dígitos que às vezes vem do Chatwoot."""
    from app.receipt_reconciliation import has_trace

    captured = {}
    table = MagicMock()
    for m in ("select", "in_", "gte", "lte", "or_", "eq", "limit"):
        getattr(table, m).return_value = table
    def _in(col, vals):
        captured["variants"] = vals
        return table
    table.in_.side_effect = _in
    table.execute = AsyncMock(return_value=MagicMock(data=[{"id": 1}]))
    client = MagicMock()
    client.from_.return_value = table

    with patch("app.receipt_reconciliation.get_supabase",
               new_callable=AsyncMock, return_value=client):
        ok = await has_trace("558171052827", int(NOW.timestamp()) - 600)

    assert ok is True
    assert "5581971052827" in captured["variants"]


async def test_has_trace_window_has_upper_bound():
    """O rastro vale só numa janela de ±15 min em torno da chegada do anexo. Sem o teto,
    um segundo comprovante processado minutos depois mascararia um primeiro que caiu
    (false-negative). Aqui conferimos que o teto (lte created_at+tolerância) é aplicado."""
    from app.receipt_reconciliation import has_trace, TRACE_TOLERANCE_S
    from app.receipt_reconciliation import _iso

    captured = {}
    table = MagicMock()
    for m in ("select", "in_", "gte", "or_", "eq", "limit"):
        getattr(table, m).return_value = table
    def _lte(col, val):
        captured["upper"] = val
        return table
    table.lte.side_effect = _lte
    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    client = MagicMock()
    client.from_.return_value = table

    created = int(NOW.timestamp()) - 600
    with patch("app.receipt_reconciliation.get_supabase",
               new_callable=AsyncMock, return_value=client):
        await has_trace("5581971052827", created)

    assert captured["upper"] == _iso(created + TRACE_TOLERANCE_S)


async def test_one_bad_candidate_does_not_abort_batch():
    """Um 429 da OpenAI (ou qualquer erro) num candidato não pode derrubar a rodada
    inteira e perder os demais — senão a rede para justamente quando os pagamentos
    também falham (memory: 429 derruba tudo)."""
    from app.receipt_reconciliation import reconcile
    cands = [_cand(attachment_id=1), _cand(attachment_id=2)]
    with patch("app.receipt_reconciliation.list_recent_incoming_attachments",
               new_callable=AsyncMock, return_value=cands), \
         patch("app.receipt_reconciliation.has_trace", new_callable=AsyncMock, return_value=False), \
         patch("app.receipt_reconciliation.already_alerted", new_callable=AsyncMock, return_value=False), \
         patch("app.receipt_reconciliation._download_bytes", new_callable=AsyncMock, return_value=b"img"), \
         patch("app.receipt_reconciliation.classify_image_raw", new_callable=AsyncMock,
               side_effect=[RuntimeError("429 insufficient_quota"), ("payment", "x")]), \
         patch("app.receipt_reconciliation.send_clinic_notification_email", new_callable=AsyncMock) as mail, \
         patch("app.receipt_reconciliation.log_event", new_callable=AsyncMock):
        n = await reconcile(NOW)
    assert n == 1
    mail.assert_awaited_once()


async def test_empty_phone_candidate_skipped():
    """Anexo sem telefone (sender sem phone_number) não gera alerta com número vazio."""
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand(phone="")], kind="payment")
    with p[0], p[1], p[2], p[3], p[4], p[5] as mail, p[6]:
        n = await reconcile(NOW)
    assert n == 0
    mail.assert_not_awaited()
