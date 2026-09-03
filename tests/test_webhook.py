"""Tests for extract_message() and the /webhook endpoint (Meta Cloud API format)."""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import os
import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import PHONE


@pytest.fixture(autouse=True)
def clear_dedup_cache():
    """Reset the global deduplication cache between tests."""
    import app.main as _main
    _main._seen_msg_ids.clear()
    yield
    _main._seen_msg_ids.clear()

# Strip @s.whatsapp.net to get the raw number Meta sends
_NUMBER = PHONE.replace("@s.whatsapp.net", "")


def _meta_payload(
    msg_type: str = "text",
    body: str = "olá",
    from_number: str = _NUMBER,
    include_messages: bool = True,
) -> dict:
    """Build a minimal Meta Cloud API webhook payload."""
    value: dict = {}
    if include_messages:
        msg: dict = {"from": from_number, "id": "wamid.test", "type": msg_type}
        if msg_type == "text":
            msg["text"] = {"body": body}
        elif msg_type == "audio":
            msg["audio"] = {"id": "media-123", "mime_type": "audio/ogg; codecs=opus"}
        elif msg_type == "image":
            msg["image"] = {"id": "media-456", "mime_type": "image/jpeg"}
        value["messages"] = [msg]

    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": value, "field": "messages"}]}],
    }


def _status_payload() -> dict:
    """Build a Meta delivery status payload (no messages key)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": {"statuses": [{"id": "wamid.test", "status": "delivered"}]}, "field": "messages"}]}],
    }


# ── extract_message tests ─────────────────────────────────────────────────────

async def test_extracts_text_message():
    from app.main import extract_message
    result = await extract_message(_meta_payload(body="Quero marcar consulta"))
    assert result is not None
    phone, text = result
    assert phone == PHONE
    assert text == "Quero marcar consulta"


async def test_ignores_status_payload():
    from app.main import extract_message
    result = await extract_message(_status_payload())
    assert result is None


async def test_ignores_empty_messages():
    from app.main import extract_message
    result = await extract_message(_meta_payload(include_messages=False))
    assert result is None


async def test_ignores_empty_text():
    from app.main import extract_message
    result = await extract_message(_meta_payload(body="   "))
    assert result is None


async def test_ignores_unknown_message_type():
    from app.main import extract_message
    result = await extract_message(_meta_payload(msg_type="sticker"))
    assert result is None


async def test_ignores_missing_from():
    from app.main import extract_message
    result = await extract_message(_meta_payload(from_number=""))
    assert result is None


async def test_audio_message_sends_notice_and_returns_none():
    """Audio messages trigger a fixed reply and are not forwarded to Eva."""
    from app.main import extract_message
    with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
        result = await extract_message(_meta_payload(msg_type="audio"))
    assert result is None
    mock_send.assert_called_once()
    assert "áudio" in mock_send.call_args[0][1].lower()


async def test_extracts_image_payment_receipt():
    """Payment receipt images reach Eva (non-None text returned)."""
    from app.main import extract_message
    with patch("app.media.process_media", new_callable=AsyncMock, return_value="[imagem]: COMPROVANTE DE PAGAMENTO: R$100"):
        result = await extract_message(_meta_payload(msg_type="image"))
    assert result is not None
    _, text = result
    assert "COMPROVANTE" in text


async def test_medical_document_image_handled_directly():
    """Medical document images (exams, laudos) return None — Eva is skipped."""
    from app.main import extract_message
    # process_media returns None when document is already handled
    with patch("app.media.process_media", new_callable=AsyncMock, return_value=None):
        result = await extract_message(_meta_payload(msg_type="image"))
    assert result is None


async def test_medical_document_pdf_handled_directly():
    """Medical document PDFs return None — Eva is skipped."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf",
            "type": "document",
            "document": {"id": "pdf-789", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    with patch("app.whatsapp.download_media", new_callable=AsyncMock, return_value=b"fake-pdf"):
        with patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value=None):
            result = await extract_message(_pdf_payload())
    assert result is None


async def test_payment_receipt_pdf_reaches_eva():
    """Payment receipt PDFs (comprovante) still reach Eva."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf2",
            "type": "document",
            "document": {"id": "pdf-abc", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    comprovante_text = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive.google.com/test]"
    with patch("app.whatsapp.download_media", new_callable=AsyncMock, return_value=b"fake-pdf"):
        with patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value=comprovante_text):
            result = await extract_message(_pdf_payload())
    assert result is not None
    _, text = result
    assert "COMPROVANTE" in text


async def test_pdf_processing_failure_notifies_clinic_only():
    """If PDF processing raises, the clinic must be notified (to follow up via a
    private Chatwoot note) and the patient must NOT be messaged directly by the bot —
    the message must never vanish without a trace."""
    from app.main import extract_message

    def _pdf_payload(from_number=_NUMBER):
        msg = {
            "from": from_number,
            "id": "wamid.pdf3",
            "type": "document",
            "document": {"id": "pdf-broken", "mime_type": "application/pdf"},
        }
        return {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
        }

    with patch("app.whatsapp.download_media", new_callable=AsyncMock, side_effect=Exception("boom")):
        with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
            with patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
                result = await extract_message(_pdf_payload())
    assert result is None
    mock_send.assert_not_called()
    mock_email.assert_called_once()


async def test_unrecognized_document_mime_notifies_clinic_only():
    """Documents with an unexpected mime_type (not 'pdf') must not be silently dropped,
    but also must not trigger a direct bot reply to the patient."""
    from app.main import extract_message

    msg = {
        "from": _NUMBER,
        "id": "wamid.doc1",
        "type": "document",
        "document": {"id": "doc-xyz", "mime_type": "application/octet-stream"},
    }
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "waba-id", "changes": [{"value": {"messages": [msg]}, "field": "messages"}]}],
    }

    with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
        with patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_email:
            result = await extract_message(payload)
    assert result is None
    mock_send.assert_not_called()
    mock_email.assert_called_once()


# ── _handle_payload dedup ordering ────────────────────────────────────────────
# Regression coverage for PR #77 (C2): dedup must run BEFORE extract_message,
# since extract_message has side effects (OpenAI vision, PDF description, the
# audio-not-supported auto-reply) that must not run twice on a Meta retry.

async def test_duplicate_image_webhook_processes_media_only_once():
    """A retried image webhook (same msg_id) must not run OpenAI vision twice."""
    from app.main import _handle_payload
    payload = _meta_payload(msg_type="image")  # id == "wamid.test"
    with patch("app.media.process_media", new_callable=AsyncMock, return_value="[imagem]: x") as mock_media, \
         patch("app.main.save_message", new_callable=AsyncMock), \
         patch("app.main.buffer_push", new_callable=AsyncMock):
        await _handle_payload(payload)
        await _handle_payload(payload)  # duplicate retry from Meta
    assert mock_media.call_count == 1


async def test_text_waits_for_the_receipt_being_read_and_both_land_in_one_turn():
    """Caso 5581991320003 (03/08/2026): "Paciente Bernardo…" seguido do comprovante
    2 s depois. O Vision levou ~9 s para ler a imagem, o debounce do texto expirou
    antes disso, e a Eva abriu um turno sem o comprovante — respondendo com a
    cobrança da taxa que a paciente acabara de pagar.

    O hold de mídia tem que segurar o texto até a imagem entrar no buffer."""
    import asyncio
    import app.buffer as buf
    from app.main import _handle_payload

    turns: list[str] = []

    async def fake_process_message(_phone: str, text: str) -> None:
        turns.append(text)

    async def slow_vision(*_args, **_kwargs) -> str:
        await asyncio.sleep(0.2)  # leitura do comprovante pelo Vision
        return "[imagem]: COMPROVANTE DE PAGAMENTO R$ 100,00"

    buf._pending.clear()
    buf._holds.clear()
    try:
        with patch("app.media.process_media", new=slow_vision), \
             patch("app.main.save_message", new_callable=AsyncMock), \
             patch("app.main.process_message", new=fake_process_message), \
             patch("app.buffer.DEBOUNCE_SECONDS", 0.03), \
             patch("app.buffer._DEFER_RETRY_SECONDS", 0.01):
            text_payload = _meta_payload(body="Paciente Bernardo Rabelo Porto Ferreira")
            text_payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.texto"
            await _handle_payload(text_payload)

            image_payload = _meta_payload(msg_type="image")
            image_payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"] = "wamid.imagem"
            await _handle_payload(image_payload)

            await asyncio.sleep(0.2)
    finally:
        buf._pending.clear()
        buf._holds.clear()

    assert len(turns) == 1, f"esperava 1 turno, veio {len(turns)}: {turns}"
    assert "Paciente Bernardo" in turns[0]
    assert "COMPROVANTE" in turns[0]


async def test_received_comprovante_is_persisted_to_messages():
    """Caso Fernanda 5587996373892 (01/09/2026): o comprovante recebido TEM que ser
    gravado em `messages`. Essa é a única fonte que as guardas de cancelamento
    (find_receipt_in_conversation no cron) leem; o evento e o Drive gravavam, mas a
    linha [imagem] do comprovante nem sempre. A gravação acontece no webhook, antes
    do buffer_push, e não pode ser removida por refactor."""
    from app.main import _handle_payload
    payload = _meta_payload(msg_type="image")
    receipt = "[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive/x]"
    with patch("app.media.process_media", new_callable=AsyncMock, return_value=receipt), \
         patch("app.main.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.main.buffer_push", new_callable=AsyncMock):
        await _handle_payload(payload)

    mock_save.assert_awaited_once()
    phone_arg, role_arg, content_arg = mock_save.await_args.args
    assert role_arg == "user"
    assert "COMPROVANTE DE PAGAMENTO" in content_arg


async def test_duplicate_audio_webhook_sends_notice_only_once():
    """A retried audio webhook (same msg_id) must not send the notice twice."""
    from app.main import _handle_payload
    payload = _meta_payload(msg_type="audio")  # id == "wamid.test"
    with patch("app.main.send_text", new_callable=AsyncMock) as mock_send:
        await _handle_payload(payload)
        await _handle_payload(payload)  # duplicate retry from Meta
    assert mock_send.call_count == 1


# ── /webhook endpoint tests ───────────────────────────────────────────────────

def _signed(body: bytes) -> str:
    secret = os.environ["META_APP_SECRET"]
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_post_returns_200(http_client):
    """POST /webhook must respond 200 immediately when the signature is valid."""
    body = json.dumps(_meta_payload()).encode()
    response = http_client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _signed(body)},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_post_rejects_missing_signature(http_client):
    """POST /webhook must respond 401 when X-Hub-Signature-256 is absent."""
    body = json.dumps(_meta_payload()).encode()
    response = http_client.post("/webhook", content=body)
    assert response.status_code == 401


def test_webhook_post_rejects_invalid_signature(http_client):
    """POST /webhook must respond 401 when the signature doesn't match the body."""
    body = json.dumps(_meta_payload()).encode()
    response = http_client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 401


def test_webhook_verify_get_returns_challenge(http_client):
    """GET /webhook must respond with the hub.challenge when token matches."""
    import os
    token = os.environ.get("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    response = http_client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "abc123"},
    )
    assert response.status_code == 200
    assert response.text == "abc123"


def test_webhook_verify_get_rejects_wrong_token(http_client):
    """GET /webhook must respond 403 when token doesn't match."""
    response = http_client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "abc123"},
    )
    assert response.status_code == 403


# ── Chatwoot webhook tests ────────────────────────────────────────────────────

def _chatwoot_payload(
    content: str = "olá",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    message_type: int | str = "incoming",
) -> dict:
    return {
        "id": 1,
        "content": content,
        "message_type": message_type,
        "event": "message_created",
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": "contact"},
    }


# ── Chatwoot webhook signature validation ─────────────────────────────────────
# Unlike /webhook (Meta), this validation is CONDITIONAL: only enforced when
# CHATWOOT_WEBHOOK_SECRET is set. It's unset in tests (see conftest.py), so the
# ten existing /chatwoot-webhook tests above and below keep working unsigned.

def _chatwoot_signed(body: bytes, secret: str, timestamp: str) -> str:
    """Reproduz o formato real do Chatwoot >= 4.11: `sha256=<hex>` sobre
    `{timestamp}.{body}` — não o hex puro do corpo sozinho."""
    message = timestamp.encode() + b"." + body
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _chatwoot_signed_headers(body: bytes, secret: str, timestamp: str | None = None) -> dict:
    ts = timestamp if timestamp is not None else str(int(time.time()))
    return {
        "X-Chatwoot-Signature": _chatwoot_signed(body, secret, ts),
        "X-Chatwoot-Timestamp": ts,
        "Content-Type": "application/json",
    }


async def test_chatwoot_webhook_skips_signature_check_when_secret_unset(async_client):
    """Backward-compat: no CHATWOOT_WEBHOOK_SECRET configured → accept without signature."""
    response = await async_client.post("/chatwoot-webhook", json=_chatwoot_payload())
    assert response.status_code == 200


async def test_chatwoot_webhook_rejects_missing_signature_when_secret_set(async_client, monkeypatch):
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    response = await async_client.post("/chatwoot-webhook", json=_chatwoot_payload())
    assert response.status_code == 403


async def test_chatwoot_webhook_rejects_invalid_signature_when_secret_set(async_client, monkeypatch):
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    response = await async_client.post(
        "/chatwoot-webhook", content=body,
        headers={
            "X-Chatwoot-Signature": "sha256=deadbeef",
            "X-Chatwoot-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 403


async def test_chatwoot_webhook_accepts_valid_signature_when_secret_set(async_client, monkeypatch):
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    with patch("app.main.buffer_push"), patch("app.main.save_message"), \
         patch("app.chatwoot.register_conversation"):
        response = await async_client.post(
            "/chatwoot-webhook", content=body,
            headers=_chatwoot_signed_headers(body, "cw-test-secret"),
        )
    assert response.status_code == 200


async def test_chatwoot_webhook_rejects_signature_without_sha256_prefix(async_client, monkeypatch):
    """O hex puro (formato antigo, errado) não pode ser aceito."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    ts = str(int(time.time()))
    bare_hex = _chatwoot_signed(body, "cw-test-secret", ts)[len("sha256="):]
    response = await async_client.post(
        "/chatwoot-webhook", content=body,
        headers={
            "X-Chatwoot-Signature": bare_hex,
            "X-Chatwoot-Timestamp": ts,
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 403


async def test_chatwoot_webhook_rejects_body_signed_without_timestamp(async_client, monkeypatch):
    """Assinar só o corpo (o que o código fazia antes) tem de ser rejeitado —
    é o que o timestamp no HMAC protege contra replay."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    body_only = hmac.new(b"cw-test-secret", body, hashlib.sha256).hexdigest()
    response = await async_client.post(
        "/chatwoot-webhook", content=body,
        headers={
            "X-Chatwoot-Signature": "sha256=" + body_only,
            "X-Chatwoot-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 403


async def test_chatwoot_webhook_rejects_missing_timestamp_header(async_client, monkeypatch):
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    headers = _chatwoot_signed_headers(body, "cw-test-secret")
    del headers["X-Chatwoot-Timestamp"]
    response = await async_client.post("/chatwoot-webhook", content=body, headers=headers)
    assert response.status_code == 403


async def test_chatwoot_webhook_rejects_stale_timestamp(async_client, monkeypatch):
    """Assinatura válida mas antiga = replay. Rejeita."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    stale = str(int(time.time()) - 3600)
    response = await async_client.post(
        "/chatwoot-webhook", content=body,
        headers=_chatwoot_signed_headers(body, "cw-test-secret", timestamp=stale),
    )
    assert response.status_code == 403


async def test_chatwoot_webhook_dryrun_never_rejects_on_mismatch(async_client, monkeypatch):
    """Modo observação: secret errado, assinatura não bate — mas o webhook passa.
    É o que permite descobrir em produção se o secret da UI é o de assinatura
    sem arriscar derrubar a Eva."""
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET_DRYRUN", "secret-que-nao-bate")
    body = json.dumps(_chatwoot_payload()).encode()
    with patch("app.main.buffer_push"), patch("app.main.save_message"), \
         patch("app.chatwoot.register_conversation"):
        response = await async_client.post(
            "/chatwoot-webhook", content=body,
            headers=_chatwoot_signed_headers(body, "outro-secret"),
        )
    assert response.status_code == 200


async def test_chatwoot_webhook_accepts_any_of_multiple_secrets(async_client, monkeypatch):
    """Dois emissores entregam no mesmo /chatwoot-webhook — o webhook de conta e o
    agent bot da Eva — cada um com secret próprio (verificado em produção: são
    diferentes). A env var aceita lista, e QUALQUER um deles precisa validar."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "secret-do-webhook, secret-do-agent-bot")
    body = json.dumps(_chatwoot_payload()).encode()
    for emissor in ("secret-do-webhook", "secret-do-agent-bot"):
        with patch("app.main.buffer_push"), patch("app.main.save_message"), \
             patch("app.chatwoot.register_conversation"):
            response = await async_client.post(
                "/chatwoot-webhook", content=body,
                headers=_chatwoot_signed_headers(body, emissor),
            )
        assert response.status_code == 200, f"emissor {emissor} foi rejeitado"


async def test_chatwoot_webhook_rejects_secret_outside_the_list(async_client, monkeypatch):
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "secret-do-webhook,secret-do-agent-bot")
    body = json.dumps(_chatwoot_payload()).encode()
    response = await async_client.post(
        "/chatwoot-webhook", content=body,
        headers=_chatwoot_signed_headers(body, "secret-de-terceiro"),
    )
    assert response.status_code == 403


async def test_chatwoot_secrets_parsing_ignores_blanks_and_spaces():
    from app.main import _chatwoot_secrets
    assert _chatwoot_secrets(" a , b ,, c ") == ["a", "b", "c"]
    assert _chatwoot_secrets("") == []
    assert _chatwoot_secrets("  ,  ") == []


async def test_chatwoot_webhook_dryrun_reports_which_secret_matched(async_client, monkeypatch, caplog):
    """O log precisa dizer QUAL secret bateu, senão não dá para saber se algum
    emissor ficou descoberto antes de promover para o modo enforce."""
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET_DRYRUN", "secret-do-webhook,secret-do-agent-bot")
    body = json.dumps(_chatwoot_payload()).encode()
    with caplog.at_level(logging.WARNING, logger="app.main"), \
         patch("app.main.buffer_push"), patch("app.main.save_message"), \
         patch("app.chatwoot.register_conversation"):
        response = await async_client.post(
            "/chatwoot-webhook", content=body,
            headers=_chatwoot_signed_headers(body, "secret-do-agent-bot"),
        )
    assert response.status_code == 200
    assert "match=True secret_n=2 de 2" in caplog.text


async def test_chatwoot_webhook_dryrun_reports_uncovered_emitter(async_client, monkeypatch, caplog):
    """Emissor não coberto aparece como match=False — é este o sinal de que ainda
    NÃO se pode promover para CHATWOOT_WEBHOOK_SECRET."""
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET_DRYRUN", "so-o-secret-do-webhook")
    body = json.dumps(_chatwoot_payload()).encode()
    with caplog.at_level(logging.WARNING, logger="app.main"), \
         patch("app.main.buffer_push"), patch("app.main.save_message"), \
         patch("app.chatwoot.register_conversation"):
        response = await async_client.post(
            "/chatwoot-webhook", content=body,
            headers=_chatwoot_signed_headers(body, "secret-do-agent-bot-nao-configurado"),
        )
    assert response.status_code == 200
    assert "match=False secret_n=None" in caplog.text


async def test_chatwoot_webhook_dryrun_logs_match_result(async_client, monkeypatch, caplog):
    monkeypatch.delenv("CHATWOOT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET_DRYRUN", "cw-test-secret")
    body = json.dumps(_chatwoot_payload()).encode()
    with caplog.at_level(logging.WARNING, logger="app.main"), \
         patch("app.main.buffer_push"), patch("app.main.save_message"), \
         patch("app.chatwoot.register_conversation"):
        response = await async_client.post(
            "/chatwoot-webhook", content=body,
            headers=_chatwoot_signed_headers(body, "cw-test-secret"),
        )
    assert response.status_code == 200
    assert "CHATWOOT_SIGNATURE_DRYRUN match=True" in caplog.text


async def test_chatwoot_webhook_enforcing_secret_wins_over_dryrun(async_client, monkeypatch):
    """Com os dois setados, o modo enforce manda — dryrun não afrouxa nada."""
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET", "cw-test-secret")
    monkeypatch.setenv("CHATWOOT_WEBHOOK_SECRET_DRYRUN", "qualquer-coisa")
    response = await async_client.post("/chatwoot-webhook", json=_chatwoot_payload())
    assert response.status_code == 403


async def test_chatwoot_webhook_processes_incoming_message(async_client):
    """Incoming patient messages via Chatwoot trigger Eva and register the conversation."""
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.chatwoot.register_conversation") as mock_register:
        mock_push.return_value = None
        mock_save.return_value = None
        mock_register.return_value = None

        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(content="Quero marcar consulta"),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_register.assert_called_once_with("5511999999999@s.whatsapp.net", 42)
        mock_push.assert_called_once()


async def test_chatwoot_webhook_ignores_outgoing_messages(async_client):
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(message_type=1),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


async def test_chatwoot_webhook_ignores_activity_messages(async_client):
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_payload(message_type=2),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


async def test_chatwoot_webhook_ignores_missing_content(async_client):
    payload = _chatwoot_payload()
    payload["content"] = ""
    with patch("app.main.buffer_push") as mock_push:
        mock_push.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


async def test_chatwoot_webhook_processes_audio_attachment(async_client):
    payload = _chatwoot_payload(content="")
    payload["attachments"] = [
        {"file_type": "audio", "data_url": "https://storage.example.com/audio.ogg"}
    ]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value="[áudio transcrito]: consulta amanhã") as mock_att:
        mock_push.return_value = None
        mock_save.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_att.assert_called_once()
        mock_push.assert_called_once()


async def test_chatwoot_webhook_processes_attachment_with_caption(async_client):
    """Regression guard: a comprovante/document sent WITH a caption (e.g. 'segue o
    comprovante') must still have its attachment processed — not just the caption
    text. Previously the attachment was only processed when the message had no
    caption at all, silently dropping the image/PDF whenever text was present."""
    payload = _chatwoot_payload(content="segue o comprovante")
    payload["attachments"] = [
        {"file_type": "image", "data_url": "https://storage.example.com/img.jpg"}
    ]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock,
               return_value="[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive.google.com/file/d/x/view]") as mock_att:
        mock_push.return_value = None
        mock_save.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_att.assert_called_once()
        mock_push.assert_called_once()
        pushed_text = mock_push.call_args[0][1]
        assert "segue o comprovante" in pushed_text
        assert "[drive_link:" in pushed_text


async def test_chatwoot_webhook_document_with_caption_falls_back_to_caption(async_client):
    """When the attachment is a medical document already fully handled (thank-you
    sent, clinic notified — describe_image_bytes returns None), the caption sent
    alongside it should still reach Eva instead of being silently discarded."""
    payload = _chatwoot_payload(content="segue meu exame, pode encaminhar?")
    payload["attachments"] = [
        {"file_type": "image", "data_url": "https://storage.example.com/exame.jpg"}
    ]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value=None) as mock_att:
        mock_push.return_value = None
        mock_save.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_att.assert_called_once()
        mock_push.assert_called_once()
        pushed_text = mock_push.call_args[0][1]
        assert pushed_text == "segue meu exame, pode encaminhar?"


async def test_process_chatwoot_attachments_forwards_phone_for_image():
    """Image attachments must carry the patient's phone into describe_image_bytes —
    otherwise a comprovante gets filed under a generic 'paciente' name and, worse, a
    medical document's thank-you/clinic-notify block (which requires phone) silently
    does nothing."""
    from app.main import _process_chatwoot_attachments
    attachments = [{"file_type": "image", "data_url": "https://storage.example.com/img.jpg"}]

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("app.media.describe_image_bytes", new_callable=AsyncMock, return_value="[imagem]: ok") as mock_describe:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.content = b"fake-image-bytes"
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _process_chatwoot_attachments(attachments, phone="5511999999999@s.whatsapp.net")

    assert result == "[imagem]: ok"
    mock_describe.assert_awaited_once_with(b"fake-image-bytes", phone="5511999999999@s.whatsapp.net")


async def test_process_chatwoot_attachments_forwards_phone_for_pdf():
    """PDF attachments must also carry phone into describe_pdf_bytes (same reasoning
    as the image case: medical-document handling depends on phone being present)."""
    from app.main import _process_chatwoot_attachments
    attachments = [{"file_type": "file", "content_type": "application/pdf",
                     "data_url": "https://storage.example.com/doc.pdf"}]

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value="[imagem]: ok") as mock_describe:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.content = b"fake-pdf-bytes"
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _process_chatwoot_attachments(attachments, phone="5511999999999@s.whatsapp.net")

    assert result == "[imagem]: ok"
    mock_describe.assert_awaited_once_with(b"fake-pdf-bytes", phone="5511999999999@s.whatsapp.net")


# ── Detecção de PDF em anexo ──────────────────────────────────────────────────
# Formatos conferidos contra anexos reais da instância (Chatwoot 4.16.2): o campo
# é `extension` (não `file_extension`), vem None na maioria dos anexos, e o
# data_url é sempre uma URL de redirect do ActiveStorage, sem extensão.


def test_attachment_is_pdf_by_content_type():
    from app.main import _attachment_is_pdf
    assert _attachment_is_pdf({
        "content_type": "application/pdf", "extension": None,
        "data_url": "https://cw.example.com/rails/active_storage/blobs/redirect/eyJfcmFpbHMi",
    })


def test_attachment_is_pdf_by_extension_when_content_type_is_generic():
    """O ganho real do campo `extension`: WhatsApp entregando PDF como
    application/octet-stream, caso em que o content_type não salva."""
    from app.main import _attachment_is_pdf
    assert _attachment_is_pdf({
        "content_type": "application/octet-stream", "extension": "pdf",
        "data_url": "https://cw.example.com/rails/active_storage/blobs/redirect/eyJfcmFpbHMi",
    })


def test_attachment_is_pdf_accepts_extension_with_leading_dot():
    from app.main import _attachment_is_pdf
    assert _attachment_is_pdf({"content_type": "", "extension": ".PDF", "data_url": ""})


def test_attachment_is_pdf_rejects_non_pdf():
    from app.main import _attachment_is_pdf
    assert not _attachment_is_pdf({
        "content_type": "image/jpeg", "extension": None,
        "data_url": "https://cw.example.com/rails/active_storage/blobs/redirect/eyJfcmFpbHMi",
    })


def test_attachment_is_pdf_tolerates_missing_fields():
    from app.main import _attachment_is_pdf
    assert not _attachment_is_pdf({})


async def test_process_chatwoot_attachments_detects_pdf_via_extension():
    """PDF com content_type genérico é processado como PDF, não cai no
    fallback '[pdf-recebido]'."""
    from app.main import _process_chatwoot_attachments
    attachments = [{
        "file_type": "file", "content_type": "application/octet-stream", "extension": "pdf",
        "data_url": "https://cw.example.com/rails/active_storage/blobs/redirect/eyJfcmFpbHMi",
    }]

    with patch("httpx.AsyncClient") as mock_client_cls, \
         patch("app.media.describe_pdf_bytes", new_callable=AsyncMock, return_value="[pdf]: ok") as mock_describe:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_resp.content = b"fake-pdf-bytes"
        mock_client = mock_client_cls.return_value.__aenter__.return_value
        mock_client.get = AsyncMock(return_value=mock_resp)

        result = await _process_chatwoot_attachments(attachments, phone="5511999999999@s.whatsapp.net")

    assert result == "[pdf]: ok"
    mock_describe.assert_awaited_once()


async def test_chatwoot_webhook_passes_phone_to_attachment_processing(async_client):
    """The webhook handler must pass the extracted phone through to
    _process_chatwoot_attachments (regression guard for the missing-phone bug)."""
    payload = _chatwoot_payload(content="", phone="+5511999999999")
    payload["attachments"] = [{"file_type": "audio", "data_url": "https://storage.example.com/audio.ogg"}]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main.save_message") as mock_save, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value="[áudio transcrito]: consulta amanhã") as mock_att:
        mock_push.return_value = None
        mock_save.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        _, kwargs = mock_att.call_args
        assert kwargs.get("phone")


async def test_chatwoot_webhook_ignores_empty_attachment(async_client):
    payload = _chatwoot_payload(content="")
    payload["attachments"] = [{"file_type": "audio", "data_url": ""}]
    with patch("app.main.buffer_push") as mock_push, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock, return_value=None) as mock_att:
        mock_push.return_value = None
        response = await async_client.post("/chatwoot-webhook", json=payload)
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_push.assert_not_called()


def _chatwoot_private_note_payload(
    content: str = "Marque a consulta com Dra. Bruna",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    sender_type: str = "user",
    private: bool = True,
) -> dict:
    return {
        "id": 2,
        "content": content,
        "message_type": "outgoing",
        "private": private,
        "event": "message_created",
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": sender_type},
    }


async def test_chatwoot_private_note_from_agent_triggers_attendant_note(async_client):
    """A private note from a human agent (sender.type == 'user'/'agent') must be
    routed to Eva as an instruction and recorded in events for traceability."""
    with patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        response = await async_client.post(
            "/chatwoot-webhook",
            json=_chatwoot_private_note_payload(sender_type="user"),
        )
        assert response.status_code == 200
        await asyncio.sleep(0.05)
        mock_note.assert_called_once()
        mock_log.assert_not_called()  # only the ignored-sender path logs directly here


async def test_chatwoot_private_note_unexpected_sender_is_logged_not_dropped():
    """A private note whose sender.type isn't 'user'/'agent' (e.g. an unexpected
    Chatwoot sender shape) must not be silently dropped — it's persisted to the
    events table so the incident is diagnosable from the DB, without server logs."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_private_note_payload(sender_type="something_unexpected")
    with patch("app.main._handle_attendant_note", new_callable=AsyncMock) as mock_note, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        await _handle_chatwoot_payload(payload)
    mock_note.assert_not_called()
    mock_log.assert_called_once()
    args = mock_log.call_args[0]
    assert args[0] == "attendant_note_ignored_unexpected_sender"
    assert args[1] == "5511999999999@s.whatsapp.net"


async def test_attendant_note_suppressed_when_eva_paused():
    """Uma nota privada da atendente NÃO pode disparar a Eva quando a conversa está
    pausada (eva-inativa / active=False). Nesse estado a atendente assumiu o
    atendimento e a nota é coordenação interna. A nota é registrada para rastreio,
    mas a Eva não fala com o paciente. Caso Rayssa 558399495410, 03/09/2026."""
    from datetime import datetime, timezone
    from app.main import _handle_attendant_note

    inactive_user = {
        "id": "u1",
        "active": False,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload = _chatwoot_private_note_payload(sender_type="user")
    with patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[inactive_user]), \
         patch("app.main.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        await _handle_attendant_note(payload)

    mock_push.assert_not_called()
    logged = [c.args[0] for c in mock_log.call_args_list]
    assert "attendant_note_suppressed_paused" in logged
    assert "attendant_note_received" not in logged


async def test_attendant_note_triggers_eva_when_active():
    """Regressão: com a conversa ATIVA, a nota privada continua steerando a Eva."""
    from app.main import _handle_attendant_note

    active_user = {"id": "u1", "active": True}
    payload = _chatwoot_private_note_payload(sender_type="user")
    with patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[active_user]), \
         patch("app.main.get_contact_by_phone", new_callable=AsyncMock, return_value=None), \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push, \
         patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        await _handle_attendant_note(payload)

    mock_push.assert_called_once()
    logged = [c.args[0] for c in mock_log.call_args_list]
    assert "attendant_note_received" in logged


def _chatwoot_public_agent_payload(
    message_id: int = 501,
    content: str = "Bom dia! Aqui é a Débora, secretária da clínica.",
    event: str | None = "message_created",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
) -> dict:
    """A public (non-private) message sent by a human attendant in Chatwoot."""
    payload = {
        "id": message_id,
        "content": content,
        "message_type": "outgoing",
        "private": False,
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": "user"},
    }
    if event is not None:
        payload["event"] = event
    return payload


async def test_chatwoot_public_agent_message_is_saved_once(mock_chatbot):
    """A public message from the attendant is mirrored into the checkpoint so Eva has
    the context — exactly once."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    await _handle_chatwoot_payload(_chatwoot_public_agent_payload(message_id=1001))

    mock_chatbot.aupdate_state.assert_called_once()
    injected = mock_chatbot.aupdate_state.call_args[0][1]["messages"]
    assert injected[0].content.startswith("Bom dia!")


async def test_chatwoot_public_agent_message_deduped_across_deliveries(mock_chatbot):
    """The same message_created reaches /chatwoot-webhook twice — once via the account
    webhook and once via the agent bot's outgoing_url, both pointing at the same URL.
    Only the first delivery may be written to the checkpoint."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    payload = _chatwoot_public_agent_payload(message_id=1002)
    await _handle_chatwoot_payload(payload)
    await _handle_chatwoot_payload(dict(payload))

    mock_chatbot.aupdate_state.assert_called_once()


async def test_chatwoot_public_agent_message_updated_is_not_resaved(mock_chatbot):
    """Chatwoot fires message_updated on every delivery-status change (sent → delivered
    → read). Those must never append another copy of the same text to the thread."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    await _handle_chatwoot_payload(_chatwoot_public_agent_payload(message_id=1003))
    for _ in range(3):
        await _handle_chatwoot_payload(
            _chatwoot_public_agent_payload(message_id=1003, event="message_updated")
        )

    mock_chatbot.aupdate_state.assert_called_once()


async def test_chatwoot_public_agent_message_updated_alone_is_ignored(mock_chatbot):
    """A message_updated whose message_created was never seen (e.g. bot restarted) must
    not be synced either — status updates are not new content."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    await _handle_chatwoot_payload(
        _chatwoot_public_agent_payload(message_id=1004, event="message_updated")
    )

    mock_chatbot.aupdate_state.assert_not_called()


async def test_chatwoot_distinct_public_agent_messages_are_both_saved(mock_chatbot):
    """Dedup is per Chatwoot message id — two different messages from the attendant,
    even with identical text, must both reach the checkpoint."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    await _handle_chatwoot_payload(_chatwoot_public_agent_payload(message_id=1005, content="Oi"))
    await _handle_chatwoot_payload(_chatwoot_public_agent_payload(message_id=1006, content="Oi"))

    assert mock_chatbot.aupdate_state.call_count == 2


async def test_chatwoot_public_agent_message_without_id_still_saved(mock_chatbot):
    """Payloads without a message id (older/agent-bot shapes) must keep the previous
    behaviour — better a rare duplicate than losing the attendant's message."""
    from app.main import _handle_chatwoot_payload

    mock_chatbot.aupdate_state = AsyncMock()
    payload = _chatwoot_public_agent_payload(message_id=1007)
    payload.pop("id")
    await _handle_chatwoot_payload(payload)

    mock_chatbot.aupdate_state.assert_called_once()


def _chatwoot_delivery_status_payload(
    status: str = "failed",
    content: str = "Olá! Esperamos que a consulta tenha sido boa!",
    phone: str = "+5511999999999",
    conversation_id: int = 42,
    sender_type: str = "agent_bot",
    private: bool = False,
    external_error: str | None = "131049 - This message was not delivered to maintain healthy ecosystem engagement.",
) -> dict:
    return {
        "id": 99,
        "content": content,
        "message_type": "outgoing",
        "private": private,
        "status": status,
        "event": "message_updated",
        "content_attributes": {"external_error": external_error} if external_error else {},
        "conversation": {
            "id": conversation_id,
            "meta": {"sender": {"phone_number": phone}},
        },
        "sender": {"phone_number": phone, "type": sender_type},
    }


async def test_chatwoot_delivery_failure_logs_event_and_notifies_agent():
    """When Meta rejects a template send asynchronously (e.g. error 131049), Chatwoot
    reports it later via message_updated/status=failed. Since the original send call
    already got a 200 from Chatwoot and moved on (see app/chatwoot.py send_template_message),
    this is the only place such failures can be caught — it must log the event and alert
    the clinic via a private note instead of silently dropping it."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload()
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_called_once()
    args = mock_log.call_args[0]
    assert args[0] == "outbound_message_delivery_failed"
    assert args[1] == "5511999999999@s.whatsapp.net"
    assert args[2]["conversation_id"] == 42
    assert "131049" in args[2]["error"]

    mock_note.assert_called_once()
    note_args = mock_note.call_args[0]
    assert note_args[0] == 42
    assert "131049" in note_args[1]


async def test_chatwoot_delivery_status_sent_is_not_flagged():
    """A normal status update (e.g. status=sent/delivered) must not be treated as a
    failure — only status=failed triggers the alert."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload(status="delivered", external_error=None)
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_not_called()
    mock_note.assert_not_called()


async def test_chatwoot_delivery_failure_ignores_human_agent_messages():
    """A failed status on a message sent by a human agent (not our automation) should
    not trigger the alert — the agent already knows their own message failed."""
    from app.main import _handle_chatwoot_payload

    payload = _chatwoot_delivery_status_payload(sender_type="agent")
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.chatwoot.add_private_note", new_callable=AsyncMock) as mock_note:
        await _handle_chatwoot_payload(payload)

    mock_log.assert_not_called()
    mock_note.assert_not_called()


# ── conversation_updated label changes (eva-ativa / eva-inativa) ───────────────

_LABEL_PHONE = "+5511999999999"
_LABEL_PHONE_JID = "5511999999999@s.whatsapp.net"


def _conv_updated_payload(
    previous_labels: list[str],
    current_labels: list[str],
    conversation_id: int = 42,
    include_label_list: bool = True,
) -> dict:
    """Real conversation_updated shape: the conversation IS the payload (no nested
    "conversation" key), and label changes arrive as label_list/cached_label_list
    entries inside changed_attributes — never as "labels"."""
    changed: list[dict] = [
        {"updated_at": {"previous_value": 1753723751, "current_value": 1753723752}},
        {"cached_label_list": {
            "previous_value": ",".join(previous_labels),
            "current_value": ",".join(current_labels),
        }},
    ]
    if include_label_list:
        changed.append({"label_list": {
            "previous_value": previous_labels,
            "current_value": current_labels,
        }})

    return {
        "event": "conversation_updated",
        "id": conversation_id,
        "inbox_id": 1,
        "status": "open",
        "can_reply": True,
        "channel": "Channel::Whatsapp",
        "additional_attributes": {},
        "contact_inbox": {"source_id": _LABEL_PHONE},
        "messages": [],
        "labels": current_labels,
        "meta": {"sender": {"phone_number": _LABEL_PHONE}},
        "changed_attributes": changed,
    }


async def test_conv_updated_eva_inativa_added_pauses():
    """Adding eva-inativa via conversation_updated must pause Eva for that patient."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-inativa"])
    with patch("app.main._pause_bot_for_patient", new_callable=AsyncMock) as mock_pause, \
         patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume:
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_pause.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_resume.assert_not_awaited()


async def test_conv_updated_eva_inativa_removed_resumes():
    """Removing eva-inativa via conversation_updated must resume Eva — this is the
    resume path that was silently dropped, leaving contacts.active False."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=["eva-inativa"], current_labels=[])
    with patch("app.main._pause_bot_for_patient", new_callable=AsyncMock) as mock_pause, \
         patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume:
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_pause.assert_not_awaited()


async def test_conv_updated_eva_ativa_added_resumes_and_reprocesses():
    """Adding eva-ativa resumes Eva and reprocesses the last patient message; the
    conversation id must be read from the top level of the payload."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"])
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "oi, tudo bem?", "attachments": [], "created_at": 100, "last_note_at": None,
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_last.assert_awaited_once_with(42)
    assert mock_push.await_args[0][0] == _LABEL_PHONE_JID
    assert mock_push.await_args[0][1] == "oi, tudo bem?"


async def test_conv_updated_eva_ativa_skips_replay_when_note_is_newer():
    """A atendente respondeu o paciente na mão, mandou a nota "Eva, agende..." e só
    então devolveu a conversa. A nota já virou um turno; reprocessar a última mensagem
    do paciente faz a Eva mandar a confirmação e o PIX duas vezes (caso 5581979037093,
    05/08/2026: "Presencial" 12:17:57 → nota 12:18:09 → eva-ativa 12:18:27)."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4201)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "Presencial", "attachments": [], "created_at": 100, "last_note_at": 112,
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_push.assert_not_awaited()


async def test_conv_updated_eva_ativa_replays_when_note_is_older():
    """Nota antiga não bloqueia nada: se o paciente escreveu DEPOIS dela, a mensagem
    continua sem resposta e é exatamente o que a reativação existe para recuperar."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4202)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "Pode ser amanhã?", "attachments": [], "created_at": 200, "last_note_at": 112,
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    assert mock_push.await_args[0][1] == "Pode ser amanhã?"


async def test_conv_updated_eva_ativa_skipped_replay_does_not_read_attachments():
    """O comprovante já foi tratado no turno da nota — reprocessá-lo aqui gastaria uma
    leitura do Vision e ainda produziria a segunda resposta."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4203)
    attachments = [{"file_type": "image", "data_url": "https://cw.example/comprovante.jpg"}]
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock) as mock_process, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "", "attachments": attachments, "created_at": 100, "last_note_at": 112,
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_process.assert_not_awaited()
    mock_push.assert_not_awaited()


async def test_conv_updated_eva_ativa_added_reprocesses_attachment_only_receipt():
    """A comprovante sent with no caption has empty `content` in Chatwoot — reactivating
    Eva via eva-ativa must still pick it up and reclassify the attachment, not just the
    (empty) text. Regression test for a receipt from 5581994358739 that was never
    recovered after the attendant added the eva-ativa label."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"])
    attachments = [{"file_type": "image", "data_url": "https://cw.example/comprovante.jpg"}]
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock) as mock_process, \
         patch("app.main.get_last_user_message", new_callable=AsyncMock, return_value=None), \
         patch("app.main.save_message", new_callable=AsyncMock), \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "", "attachments": attachments, "created_at": 100, "last_note_at": None,
        }
        mock_process.return_value = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive/x]"
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_process.assert_awaited_once_with(attachments, phone=_LABEL_PHONE_JID)
    assert mock_push.await_args[0][0] == _LABEL_PHONE_JID
    assert mock_push.await_args[0][1] == "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive/x]"


async def test_conv_updated_eva_ativa_saves_receipt_when_not_yet_persisted():
    """A brecha de persistência: um comprovante que chegou só pelo Chatwoot enquanto a
    conversa estava eva-inativa nunca virou linha em `messages` (o handler chatwoot
    retorna cedo antes do save). Ao reativar a Eva pelo label eva-ativa, o replay
    re-lê o anexo (evento + Drive) mas antes NÃO gravava a mensagem — cegando
    find_receipt_in_conversation no cron de cobrança. Agora grava, uma única vez."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4210)
    attachments = [{"file_type": "image", "data_url": "https://cw.example/comprovante.jpg"}]
    receipt = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive/x]"
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock) as mock_process, \
         patch("app.main.get_last_user_message", new_callable=AsyncMock) as mock_last_saved, \
         patch("app.main.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "", "attachments": attachments, "created_at": 100, "last_note_at": None,
        }
        mock_process.return_value = receipt
        # última mensagem "user" gravada NÃO é um comprovante → o comprovante do
        # replay ainda não está em `messages`.
        mock_last_saved.return_value = "boa tarde"
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_save.assert_awaited_once_with(_LABEL_PHONE_JID, "user", receipt)
    assert mock_push.await_args[0][1] == receipt


async def test_conv_updated_eva_ativa_does_not_resave_receipt_already_persisted():
    """Caminho comum: o comprovante já foi gravado na primeira entrega (webhook Meta
    ou chatwoot). O replay re-roda o Vision e gera um novo drive_link a cada vez, então
    comparar texto exato não serve — o sinal robusto é "a última mensagem 'user' já é
    um comprovante". Nesse caso NÃO gravamos de novo, senão criaríamos uma segunda
    linha [imagem] duplicada."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4211)
    attachments = [{"file_type": "image", "data_url": "https://cw.example/comprovante.jpg"}]
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main._process_chatwoot_attachments", new_callable=AsyncMock) as mock_process, \
         patch("app.main.get_last_user_message", new_callable=AsyncMock) as mock_last_saved, \
         patch("app.main.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "", "attachments": attachments, "created_at": 100, "last_note_at": None,
        }
        # novo drive_link, texto diferente do que já está salvo...
        mock_process.return_value = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive/NEW]"
        # ...mas a última mensagem "user" já é um comprovante (drive_link antigo).
        mock_last_saved.return_value = "[imagem]: COMPROVANTE DE PAGAMENTO: R$100 [drive_link:https://drive/OLD]"
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_save.assert_not_awaited()
    assert mock_push.await_args[0][1].startswith("[imagem]: COMPROVANTE DE PAGAMENTO")


async def test_conv_updated_eva_ativa_does_not_save_plain_text_replay():
    """O save do replay é limitado a comprovantes de propósito: é a única perda que cega
    o cron de cobrança. Uma mensagem de texto comum reprocessada não deve ser gravada
    (evita duplicar histórico), só encaminhada ao buffer."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["eva-ativa"], conversation_id=4212)
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.get_last_user_message", new_callable=AsyncMock) as mock_last_saved, \
         patch("app.main.save_message", new_callable=AsyncMock) as mock_save, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {
            "content": "pode ser amanhã?", "attachments": [], "created_at": 100, "last_note_at": None,
        }
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_save.assert_not_awaited()
    mock_last_saved.assert_not_awaited()
    assert mock_push.await_args[0][1] == "pode ser amanhã?"


async def test_conv_updated_falls_back_to_cached_label_list():
    """Older/agent-bot payloads may carry only cached_label_list (comma-separated)."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(
        previous_labels=["eva-inativa"], current_labels=[], include_label_list=False
    )
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume:
        handled = await _handle_label_change(payload)

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)


async def test_conv_updated_unrelated_label_change_is_ignored():
    """A label change that doesn't touch the eva-* control labels changes nothing."""
    from app.main import _handle_label_change

    payload = _conv_updated_payload(previous_labels=[], current_labels=["urgente"])
    with patch("app.main._pause_bot_for_patient", new_callable=AsyncMock) as mock_pause, \
         patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume:
        handled = await _handle_label_change(payload)

    assert handled is False
    mock_pause.assert_not_awaited()
    mock_resume.assert_not_awaited()


# ── Attendant changing priority/status must not wake Eva up ────────────────────


@pytest.fixture(autouse=True)
def _clear_conv_labels():
    """The label tracker is process-global; each test starts from a clean slate."""
    from app.main import _conv_labels
    _conv_labels.clear()
    yield
    _conv_labels.clear()


def _conv_updated_non_label_payload(changed_key: str, current_labels: list[str],
                                    conversation_id: int = 42) -> dict:
    """conversation_updated fired by a priority/status change: changed_attributes
    names the field that changed and never mentions labels, while the payload still
    carries the conversation's (unchanged) labels."""
    payload = _conv_updated_payload(previous_labels=current_labels, current_labels=current_labels,
                                    conversation_id=conversation_id)
    payload["changed_attributes"] = [
        {"updated_at": {"previous_value": 1753723751, "current_value": 1753723752}},
        {changed_key: {"previous_value": None, "current_value": "urgent"}},
    ]
    return payload


@pytest.mark.parametrize("changed_key", ["priority", "status"])
async def test_conv_updated_priority_or_status_change_does_not_replay(changed_key):
    """A atendente mudou só a prioridade/status de uma conversa que já tinha a label
    eva-ativa: nada mudou nas labels, então Eva não pode reprocessar nada."""
    from app.main import _handle_label_change

    payload = _conv_updated_non_label_payload(changed_key, current_labels=["eva-ativa"])
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        handled = await _handle_label_change(payload)

    assert handled is False
    mock_resume.assert_not_awaited()
    mock_last.assert_not_awaited()
    mock_push.assert_not_awaited()


async def test_conversation_resolved_does_not_replay_labels_seen_for_the_first_time():
    """Regressão da conversa 344: a atendente marcou como resolvida e Eva respondeu
    ao comprovante do dia anterior. Labels vistas pela primeira vez são estado inicial,
    não labels recém-adicionadas."""
    from app.main import _handle_label_change

    payload = {
        "event": "conversation_resolved",
        "id": 344,
        "status": "resolved",
        "labels": ["eva-ativa"],
        "meta": {"sender": {"phone_number": _LABEL_PHONE}},
    }
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        handled = await _handle_label_change(payload)

    assert handled is False
    mock_resume.assert_not_awaited()
    mock_last.assert_not_awaited()
    mock_push.assert_not_awaited()


async def test_message_updated_still_reacts_to_a_real_eva_ativa_add():
    """Depois que as labels da conversa já são conhecidas, adicionar eva-ativa continua
    reativando Eva e reprocessando a última mensagem do paciente."""
    from app.main import _handle_label_change

    def _payload(labels: list[str]) -> dict:
        return {
            "event": "message_updated",
            "id": 999,
            "conversation": {"id": 42, "labels": labels,
                             "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
        }

    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume, \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock) as mock_push:
        mock_last.return_value = {"content": "oi", "attachments": []}
        assert await _handle_label_change(_payload([])) is False   # primeira vez: só registra
        handled = await _handle_label_change(_payload(["eva-ativa"]))

    assert handled is True
    mock_resume.assert_awaited_once_with(_LABEL_PHONE_JID)
    mock_push.assert_awaited_once()


async def test_payload_without_labels_key_does_not_reset_the_tracker():
    """Um evento que não carrega `labels` não diz nada sobre as labels da conversa —
    zerar o tracker faria o próximo evento parecer que tudo foi adicionado."""
    from app.main import _handle_label_change, _conv_labels

    seed = {
        "event": "message_updated",
        "id": 999,
        "conversation": {"id": 42, "labels": ["eva-ativa"],
                         "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
    }
    no_labels = {
        "event": "message_updated",
        "id": 1000,
        "conversation": {"id": 42, "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
    }
    with patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock), \
         patch("app.main.buffer_push", new_callable=AsyncMock):
        await _handle_label_change(seed)
        await _handle_label_change(no_labels)

    assert _conv_labels["42"] == frozenset({"eva-ativa"})


async def test_seed_carrying_a_control_label_is_logged_as_event():
    """A primeira observação de uma conversa não age — mas se ela já chega com eva-ativa
    ou eva-inativa, é exatamente o caso em que uma reativação de verdade PODE ter sido
    engolida (tracker vazio depois de um restart). Precisa virar evento no Supabase, e
    não só linha de log no container: sem isso a única forma de medir a frequência é SSH
    no VPS, dentro da janela de retenção do Docker."""
    from app.main import _handle_label_change

    payload = {
        "event": "message_updated",
        "id": 999,
        "conversation": {"id": 42, "labels": ["eva-ativa"],
                         "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
    }
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.main._resume_bot_for_patient", new_callable=AsyncMock) as mock_resume:
        assert await _handle_label_change(payload) is False

    mock_resume.assert_not_awaited()
    _names = [c.args[0] for c in mock_log.await_args_list]
    assert "label_tracker_seeded" in _names
    _meta = next(c.args[2] for c in mock_log.await_args_list if c.args[0] == "label_tracker_seeded")
    assert sorted(_meta["labels"]) == ["eva-ativa"]
    assert _meta["conversation_id"] == "42"


async def test_seed_without_control_labels_is_not_logged():
    """Semear conversa sem eva-ativa/eva-inativa é o caso comum e inofensivo — logar
    todas encheria a tabela de events com ruído e esconderia justamente as que importam."""
    from app.main import _handle_label_change

    payload = {
        "event": "message_updated",
        "id": 999,
        "conversation": {"id": 42, "labels": ["financeiro"],
                         "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
    }
    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log:
        assert await _handle_label_change(payload) is False

    assert "label_tracker_seeded" not in [c.args[0] for c in mock_log.await_args_list]


async def test_real_delta_from_tracker_is_logged_as_event():
    """Quando o caminho do tracker (e não o changed_attributes do Chatwoot) é quem
    detecta um add real de eva-ativa, isso vira evento. É a medida que responde se o
    fallback está em uso de verdade — e portanto se o risco do restart é real."""
    from app.main import _handle_label_change

    def _payload(labels: list[str]) -> dict:
        return {
            "event": "message_updated",
            "id": 999,
            "conversation": {"id": 42, "labels": labels,
                             "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
        }

    with patch("app.main.log_event", new_callable=AsyncMock) as mock_log, \
         patch("app.main._resume_bot_for_patient", new_callable=AsyncMock), \
         patch("app.chatwoot.get_last_patient_message", new_callable=AsyncMock) as mock_last, \
         patch("app.main.buffer_push", new_callable=AsyncMock):
        mock_last.return_value = {"content": "oi", "attachments": []}
        await _handle_label_change(_payload([]))                 # semeia
        await _handle_label_change(_payload(["eva-ativa"]))      # add real via tracker

    _meta = next(c.args[2] for c in mock_log.await_args_list
                 if c.args[0] == "label_delta_from_tracker")
    assert _meta["added"] == ["eva-ativa"]
    assert _meta["conversation_id"] == "42"


async def test_message_created_seeds_the_label_tracker():
    """Mensagens comuns registram as labels da conversa, para que uma label realmente
    adicionada depois seja vista como mudança e não como primeira observação."""
    from app.main import _handle_chatwoot_payload, _conv_labels

    payload = {
        "event": "message_created",
        "id": 1,
        "message_type": 0,
        "content": "oi",
        "conversation": {"id": 42, "labels": ["eva-inativa"], "status": "open",
                         "meta": {"sender": {"phone_number": _LABEL_PHONE}}},
    }
    await _handle_chatwoot_payload(payload)

    assert _conv_labels["42"] == frozenset({"eva-inativa"})


def test_security_headers_present(http_client):
    """A API expõe nosniff, Referrer-Policy e HSTS em toda resposta."""
    r = http_client.get("/health")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert "max-age=" in r.headers["Strict-Transport-Security"]
