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


async def test_untraced_document_alerts_and_marks():
    from app.receipt_reconciliation import reconcile
    p = _patches(candidates=[_cand()], traced=False, alerted=False, kind="document")
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
               side_effect=[RuntimeError("429 insufficient_quota"), ("document", "x")]), \
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


async def test_already_replayed_true_when_event_exists():
    """already_replayed consulta o evento receipt_drop_replayed pelo attachment_id."""
    from app.receipt_reconciliation import already_replayed, REPLAY_EVENT

    assert REPLAY_EVENT == "receipt_drop_replayed"
    captured = {}
    table = MagicMock()
    for m in ("select", "limit"):
        getattr(table, m).return_value = table

    def _eq(col, val):
        captured["eq"] = (col, val)
        return table

    def _contains(col, val):
        captured["contains"] = (col, val)
        return table

    table.eq.side_effect = _eq
    table.contains.side_effect = _contains
    table.execute = AsyncMock(return_value=MagicMock(data=[{"id": 1}]))
    client = MagicMock()
    client.from_.return_value = table

    with patch("app.receipt_reconciliation.get_supabase",
               new_callable=AsyncMock, return_value=client):
        assert await already_replayed(2112) is True
    assert captured["eq"] == ("event_type", "receipt_drop_replayed")
    assert captured["contains"] == ("metadata", {"attachment_id": 2112})

    table.execute = AsyncMock(return_value=MagicMock(data=[]))
    with patch("app.receipt_reconciliation.get_supabase",
               new_callable=AsyncMock, return_value=client):
        assert await already_replayed(7) is False


async def test_replay_registers_and_notifies():
    """Comprovante lido: espelha em messages, grava o marcador e roda a Eva com bypass."""
    from app.receipt_reconciliation import replay_dropped_receipt

    texto = "COMPROVANTE DE PAGAMENTO: R$ 100 [drive_link:https://d/x]"
    with patch("app.receipt_reconciliation.describe_image_bytes",
               new_callable=AsyncMock, return_value=texto) as desc, \
         patch("app.receipt_reconciliation.log_event", new_callable=AsyncMock) as logev, \
         patch("app.receipt_reconciliation._alert_clinic", new_callable=AsyncMock) as alert, \
         patch("app.main.save_message", new_callable=AsyncMock) as save, \
         patch("app.main.process_message", new_callable=AsyncMock) as proc:
        ok = await replay_dropped_receipt(_cand(), b"img")

    assert ok is True
    canon = "5581971052827@s.whatsapp.net"
    assert desc.await_args.kwargs["phone"] == canon
    logev.assert_awaited_once()
    args = logev.await_args.args
    assert args[0] == "receipt_drop_replayed"
    assert args[1] == canon
    assert args[2]["attachment_id"] == 999
    assert args[2]["conversation_id"] == 505
    save.assert_awaited_once_with(canon, "user", texto)
    proc.assert_awaited_once_with(canon, texto, bypass_pause=True)
    alert.assert_not_awaited()


async def test_replay_falls_back_to_alert_when_not_a_receipt():
    """Reclassificado como documento: alerta a clínica, sem marcador nem Eva."""
    from app.receipt_reconciliation import replay_dropped_receipt

    cand = _cand()
    with patch("app.receipt_reconciliation.describe_image_bytes",
               new_callable=AsyncMock, return_value=None), \
         patch("app.receipt_reconciliation.log_event", new_callable=AsyncMock) as logev, \
         patch("app.receipt_reconciliation._alert_clinic", new_callable=AsyncMock) as alert, \
         patch("app.main.save_message", new_callable=AsyncMock) as save, \
         patch("app.main.process_message", new_callable=AsyncMock) as proc:
        ok = await replay_dropped_receipt(cand, b"img")

    assert ok is False
    alert.assert_awaited_once_with(cand, "payment")
    logev.assert_not_awaited()
    save.assert_not_awaited()
    proc.assert_not_awaited()


def _reconcile_ctx(stack, cands, kind="payment", replayed=False, replay=None):
    """Aplica os mocks do reconcile num ExitStack e devolve (replay, mail, logev)."""
    p = _patches(candidates=cands, kind=kind)
    for x in p[:5]:
        stack.enter_context(x)
    mail = stack.enter_context(p[5])
    logev = stack.enter_context(p[6])
    stack.enter_context(patch("app.receipt_reconciliation.already_replayed",
                              new_callable=AsyncMock, return_value=replayed))
    rep = stack.enter_context(patch("app.receipt_reconciliation.replay_dropped_receipt",
                                    new_callable=AsyncMock, return_value=True) if replay is None
                              else patch("app.receipt_reconciliation.replay_dropped_receipt", replay))
    return rep, mail, logev


async def test_reconcile_payment_calls_replay():
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, [_cand()])
        await reconcile(NOW)
    rep.assert_awaited_once()
    assert rep.await_args.args[0]["attachment_id"] == 999
    assert rep.await_args.args[1] == b"img"
    mail.assert_not_awaited()


async def test_reconcile_document_still_alerts():
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, [_cand()], kind="document")
        n = await reconcile(NOW)
    assert n == 1
    rep.assert_not_awaited()
    mail.assert_awaited_once()
    assert logev.await_args.args[0] == "receipt_drop_alerted"


async def test_reconcile_skips_already_replayed():
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, [_cand()], replayed=True)
        await reconcile(NOW)
    rep.assert_not_awaited()
    mail.assert_not_awaited()


async def test_reconcile_candidate_exception_does_not_abort():
    """Falha fora do replay (ex.: classificação) num candidato não derruba os demais."""
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    cands = [_cand(attachment_id=1), _cand(attachment_id=2)]
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, cands)
        st.enter_context(patch("app.receipt_reconciliation.classify_image_raw",
                               new_callable=AsyncMock,
                               side_effect=[RuntimeError("boom"), ("payment", "x")]))
        await reconcile(NOW)
    rep.assert_awaited_once()
    assert rep.await_args.args[0]["attachment_id"] == 2


async def test_reconcile_replay_crash_alerts_clinic_and_continues():
    """Replay que lança vira alerta por e-mail (nunca comprovante enterrado em silêncio)
    e a rodada segue para o próximo candidato."""
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    cands = [_cand(attachment_id=1), _cand(attachment_id=2)]
    replay = AsyncMock(side_effect=[RuntimeError("boom"), True])
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, cands, replay=replay)
        n = await reconcile(NOW)
    assert replay.await_count == 2
    mail.assert_awaited_once()
    assert n == 1
    assert logev.await_args.args[0] == "receipt_drop_alerted"
    assert logev.await_args.args[2]["attachment_id"] == 1


async def test_reconcile_replay_false_marks_alerted_without_own_email():
    """Replay retorna False (não era comprovante; ele mesmo já mandou o e-mail): o
    reconcile só grava receipt_drop_alerted e conta 1, sem e-mail próprio."""
    from contextlib import ExitStack
    from app.receipt_reconciliation import reconcile
    replay = AsyncMock(return_value=False)
    with ExitStack() as st:
        rep, mail, logev = _reconcile_ctx(st, [_cand()], replay=replay)
        n = await reconcile(NOW)
    assert n == 1
    replay.assert_awaited_once()
    mail.assert_not_awaited()
    logev.assert_awaited_once()
    assert logev.await_args.args[0] == "receipt_drop_alerted"
