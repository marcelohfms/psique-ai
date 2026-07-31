"""Tests for app/media.py — media classification, Drive filenames, and patient lookup."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.asyncio


# ── _get_patient_name ─────────────────────────────────────────────────────────

async def test_get_patient_name_returns_full_name_not_just_first():
    """Filenames should use the patient's full name, not just the first name —
    matching the convention used for payment receipts (Nome_Completo_..._)."""
    from app.media import _get_patient_name
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Maria Aparecida Silva"}):
        result = await _get_patient_name("5511999999999@s.whatsapp.net")
    assert result == "Maria_Aparecida_Silva"


async def test_get_patient_name_strips_accents_and_collapses_spaces():
    from app.media import _get_patient_name
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "José  da  Conceição"}):
        result = await _get_patient_name("5511999999999@s.whatsapp.net")
    assert result == "Jose_da_Conceicao"


async def test_get_patient_name_falls_back_when_no_user():
    from app.media import _get_patient_name
    with patch("app.database.get_user_by_phone", new_callable=AsyncMock, return_value=None):
        result = await _get_patient_name("5511999999999@s.whatsapp.net")
    assert result == "paciente"


# ── describe_image_bytes: medical document upload ─────────────────────────────

def _mock_openai_response(text: str):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


async def test_document_upload_filename_has_full_patient_name_and_date():
    """Medical documents (exames, laudos, etc.) must be filed in Drive with the
    patient's full name and the date the document was sent — this is the only
    naming opportunity for documents (unlike payment receipts, they are never
    renamed later)."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("EXAME: hemograma completo")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "João Pedro Alves"}), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID": "folder-123"}), \
         patch("app.google_drive.upload_document", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/doc1/view") as mock_upload, \
         patch("app.whatsapp.send_text", new_callable=AsyncMock), \
         patch("app.database.save_message", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is None  # document flow bypasses Eva entirely
    mock_upload.assert_awaited_once()
    filename = mock_upload.call_args[0][1]
    assert filename.startswith("exame_Joao_Pedro_Alves_")
    import re
    assert re.search(r"_\d{2}-\d{2}-\d{4}\.jpg$", filename), filename


async def test_document_upload_notifies_clinic_with_full_patient_name():
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("LAUDO: laudo psiquiátrico")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Ana Beatriz Souza"}), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID": "folder-123"}), \
         patch("app.google_drive.upload_document", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/doc2/view"), \
         patch("app.whatsapp.send_text", new_callable=AsyncMock), \
         patch("app.database.save_message", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock) as mock_notify:
        await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    mock_notify.assert_awaited_once()
    _subject, body = mock_notify.call_args[0]
    assert "Ana Beatriz Souza" in body


# ── is_payment_receipt_message ────────────────────────────────────────────────
# Usado pelo _route_entry para levar um comprovante ao patient_agent mesmo com o
# cadastro incompleto. Precisa ser ESTRITO: só o que o classificador de visão
# marcou como CATEGORIA 1 conta — nunca "qualquer imagem".

async def test_receipt_detector_accepts_classifier_output():
    from app.media import is_payment_receipt_message
    assert is_payment_receipt_message(
        "[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, "
        "chave PIX 42.006.848/0001-78. [drive_link:https://drive.google.com/file/d/abc/view]"
    )


async def test_receipt_detector_accepts_receipt_without_drive_link():
    """Quando o upload para o Drive falha, media.py devolve a descrição sem a tag
    [drive_link:...] — continua sendo um comprovante, e register_payment sabe
    procurar o link no histórico."""
    from app.media import is_payment_receipt_message
    assert is_payment_receipt_message("[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00.")


async def test_receipt_detector_rejects_other_media():
    from app.media import is_payment_receipt_message
    assert not is_payment_receipt_message("[imagem]: LAUDO: laudo neuropsicológico de 12 páginas.")
    assert not is_payment_receipt_message("[imagem]: EXAME: hemograma completo.")
    assert not is_payment_receipt_message("[pdf-recebido]")
    assert not is_payment_receipt_message("já mandei o comprovante de pagamento ontem")
    assert not is_payment_receipt_message("")
    assert not is_payment_receipt_message(None)
