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


async def test_vision_call_uses_gpt52_params():
    """O classificador de visão usa gpt-5.2 com temperature=0 (classificação
    determinística), que rejeita max_tokens (exige max_completion_tokens) e usa
    reasoning_effort="none" — regressão aqui quebraria TODO processamento de
    comprovante/documento em produção."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("IGNORAR")
    )
    with patch("app.media._get_openai", return_value=fake_openai):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is None  # IGNORAR é descartado silenciosamente
    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.2"
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["temperature"] == 0
    assert "max_tokens" not in kwargs
    assert kwargs["max_completion_tokens"] == 300


async def test_process_media_audio_returns_none_without_api_call():
    """Áudio nunca é transcrito (a clínica não ouve áudio; o paciente recebe um
    aviso pelos webhooks). process_media não pode gastar API com áudio."""
    from app.media import process_media
    with patch("app.whatsapp.download_media", new_callable=AsyncMock) as mock_download:
        result = await process_media("media-123", "audio", phone="5511999999999@s.whatsapp.net")
    assert result is None
    mock_download.assert_not_awaited()


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


@pytest.mark.parametrize("legenda", [
    "Pagamento de sinal para consulta realizado!",
    "Segue comprovante de antecipação ref consulta p 13/07, às 11 h.",
    "Pagamento ok",
    "Paguei no dia 01 de julho",
])
async def test_receipt_detector_accepts_receipt_sent_with_a_caption(legenda):
    """Os dois webhooks montam o texto como `f"{caption}\\n{descrição}"`
    (app/main.py) — o comprovante quase nunca chega no início da string. Ancorar em
    startswith() deixava esses casos invisíveis para o _route_entry, que é
    exatamente o furo do caso Bernardo. As legendas acima são reais, extraídas da
    tabela `messages` na auditoria de 31/07/2026."""
    from app.media import is_payment_receipt_message
    assert is_payment_receipt_message(
        f"{legenda}\n[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00."
    )


async def test_receipt_detector_accepts_receipt_after_another_image():
    """O buffer de debounce junta tudo o que chegou na janela com um espaço
    (app/buffer.py), então o comprovante pode vir depois de outra imagem."""
    from app.media import is_payment_receipt_message
    assert is_payment_receipt_message(
        "[imagem]: EXAME: hemograma completo. [imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00."
    )


async def test_receipt_detector_accepts_receipt_followed_by_text():
    from app.media import is_payment_receipt_message
    assert is_payment_receipt_message(
        "[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00. já paguei, pode confirmar?"
    )


async def test_receipt_detector_still_requires_the_image_tag():
    """Continua estrito: texto solto mencionando comprovante não conta — quem
    digita 'segue o comprovante' sem anexar nada não pode disparar register_payment
    nem bloquear o cancelamento automático."""
    from app.media import is_payment_receipt_message
    assert not is_payment_receipt_message("Segue o comprovante de pagamento")
    assert not is_payment_receipt_message("Comprovante de pagamento")
    assert not is_payment_receipt_message(
        "Acabo de enviar acima o comprovante de pagamento via pix no valor de R$450,00"
    )
    assert not is_payment_receipt_message("[imagem]: EXAME: pedido de comprovante de pagamento antigo")


# ── classify_media_description ────────────────────────────────────────────────
# Caso Igor Lapsky (27/08/2026): um comprovante Pix perfeitamente legível foi
# roteado para o ramo de documento porque a resposta do classificador não começava
# EXATAMENTE com "COMPROVANTE DE PAGAMENTO:". O startswith ancorado derruba
# qualquer enfeite (markdown, rótulo "CATEGORIA 1", aspas) no fallback "documento",
# em silêncio. Esta função interpreta a resposta crua com tolerância a decoração e
# resgata comprovantes descritos sob prefixo errado.

def test_classify_canonical_receipt_is_payment():
    from app.media import classify_media_description
    kind, canonical = classify_media_description(
        "COMPROVANTE DE PAGAMENTO: valor transferido R$ 550,00, PIX 42.006.848/0001-78"
    )
    assert kind == "payment"
    assert canonical.upper().startswith("COMPROVANTE DE PAGAMENTO")


@pytest.mark.parametrize("decorated", [
    "**COMPROVANTE DE PAGAMENTO:** valor transferido R$ 550,00",
    "'COMPROVANTE DE PAGAMENTO:' valor transferido R$ 550,00",
    "CATEGORIA 1 — COMPROVANTE DE PAGAMENTO: valor transferido R$ 550,00",
    "Categoria 1: COMPROVANTE DE PAGAMENTO: valor transferido R$ 550,00",
    "  \nCOMPROVANTE DE PAGAMENTO: valor transferido R$ 550,00",
])
def test_classify_decorated_receipt_still_payment(decorated):
    """Enfeite cosmético na resposta do modelo não pode mudar a rota."""
    from app.media import classify_media_description, is_payment_receipt_message
    kind, canonical = classify_media_description(decorated)
    assert kind == "payment"
    # o texto canônico precisa continuar reconhecível pelo detector downstream
    assert is_payment_receipt_message(f"[imagem]: {canonical}")


def test_classify_receipt_misfiled_as_documento_is_rescued():
    """O provável formato do caso Igor: prefixo de documento, corpo de comprovante."""
    from app.media import classify_media_description, is_payment_receipt_message
    kind, canonical = classify_media_description(
        "DOCUMENTO: comprovante de transferência Pix do Bradesco no valor de "
        "R$ 550,00 para PSIQUE, CNPJ 42.006.848/0001-78"
    )
    assert kind == "payment"
    assert is_payment_receipt_message(f"[imagem]: {canonical}")
    # o corpo original é preservado para o register_payment extrair valor/data
    assert "550,00" in canonical


def test_classify_no_known_prefix_but_receipt_content_is_rescued():
    from app.media import classify_media_description
    kind, _ = classify_media_description(
        "A imagem mostra um comprovante de pagamento via PIX de R$ 100,00."
    )
    assert kind == "payment"


def test_classify_medical_document_stays_document():
    from app.media import classify_media_description
    for desc in [
        "EXAME: hemograma completo",
        "LAUDO: laudo psiquiátrico de 3 páginas",
        "DOCUMENTO: carteira de identidade do paciente",
    ]:
        kind, canonical = classify_media_description(desc)
        assert kind == "document", desc
        assert canonical == desc


def test_classify_ignorar_with_decoration_is_ignored():
    from app.media import classify_media_description
    assert classify_media_description("IGNORAR")[0] == "ignore"
    assert classify_media_description("**IGNORAR**")[0] == "ignore"


def test_classify_unrecognized_output_falls_back_to_document():
    from app.media import classify_media_description
    kind, _ = classify_media_description("Não consegui identificar esta imagem.")
    assert kind == "document"


# ── describe_image_bytes usa o classificador tolerante ────────────────────────

async def test_decorated_receipt_reply_routes_to_payment_branch(caplog):
    """Resposta com markdown não pode cair no ramo de documento (caso Igor)."""
    import logging
    from app.media import describe_image_bytes, is_payment_receipt_message
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("**COMPROVANTE DE PAGAMENTO:** Pix R$ 550,00")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": ""}), \
         caplog.at_level(logging.INFO, logger="app.media"):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is not None
    assert is_payment_receipt_message(result)


async def test_documento_prefixed_receipt_is_rescued_and_logged(caplog):
    import logging
    from app.media import describe_image_bytes, is_payment_receipt_message
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response(
            "DOCUMENTO: comprovante de transferência Pix de R$ 550,00 para PSIQUE"
        )
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": ""}), \
         caplog.at_level(logging.INFO, logger="app.media"):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is not None
    assert is_payment_receipt_message(result)
    assert any("IMAGE_CLASSIFIER_RESCUED_AS_PAYMENT" in r.message for r in caplog.records)


async def test_classifier_raw_output_is_always_logged(caplog):
    """A resposta crua do modelo tem que ficar no log — no caso Igor ela só
    existia no e-mail da clínica, impossível de auditar depois."""
    import logging
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("IGNORAR")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         caplog.at_level(logging.INFO, logger="app.media"):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is None
    assert any("IMAGE_CLASSIFIER_RAW" in r.message for r in caplog.records)


async def test_unrecognized_reply_still_documents_but_warns(caplog):
    import logging
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("Um papel impresso com texto ilegível.")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Fulano De Tal"}), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_DOCUMENTS_FOLDER_ID": "folder-123"}), \
         patch("app.google_drive.upload_document", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/doc9/view") as mock_upload, \
         patch("app.whatsapp.send_text", new_callable=AsyncMock), \
         patch("app.database.save_message", new_callable=AsyncMock), \
         patch("app.email_sender.send_clinic_notification_email", new_callable=AsyncMock), \
         caplog.at_level(logging.INFO, logger="app.media"):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert result is None  # segue o fluxo de documento (não some com o arquivo)
    filename = mock_upload.call_args[0][1]
    assert filename.startswith("documento_")
    assert any("IMAGE_CLASSIFIER_UNRECOGNIZED" in r.message for r in caplog.records)


# ── Releitura reforçada do valor do comprovante ───────────────────────────────

def test_find_brl_amount_pega_valor_e_ignora_ilegivel():
    from app.media import _find_brl_amount, _description_has_amount
    assert _find_brl_amount("valor transferido R$ 100,00 chave...") == "100,00"
    assert _find_brl_amount("Total R$ 1.234,56 pago") == "1.234,56"
    assert _find_brl_amount("R$ 100 via PIX") == "100"
    assert _find_brl_amount("COMPROVANTE: valor R$ ? não identificado") is None
    assert _find_brl_amount("sem valor algum") is None
    assert _description_has_amount("COMPROVANTE DE PAGAMENTO: R$ 100,00") is True
    assert _description_has_amount("COMPROVANTE DE PAGAMENTO: valor ilegível") is False


async def test_describe_image_reread_recovers_amount():
    """1ª leitura não traz valor → 2ª leitura focada recupera → vai pra descrição."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(side_effect=[
        _mock_openai_response("COMPROVANTE DE PAGAMENTO: PIX para PSIQUE, valor não visível"),
        _mock_openai_response("100,00"),  # releitura focada
    ])
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Maria"}), \
         patch("app.google_drive.upload_image", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/rcpt/view"), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": "folder-1"}):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert fake_openai.chat.completions.create.await_count == 2  # releu
    assert "R$ 100,00" in result  # valor recuperado entrou na descrição


async def test_describe_image_no_reread_when_amount_already_present():
    """1ª leitura já traz o valor → NÃO faz segunda chamada."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(
        return_value=_mock_openai_response("COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, PIX PSIQUE")
    )
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Maria"}), \
         patch("app.google_drive.upload_image", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/rcpt/view"), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": "folder-1"}):
        result = await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    assert fake_openai.chat.completions.create.await_count == 1  # não releu
    assert "R$ 100,00" in result


async def test_reread_amount_uses_gpt4o_not_gpt5():
    """A releitura do VALOR volta para o gpt-4o: os dois únicos comprovantes
    ilegíveis (03/09/2026) aconteceram no gpt-5.2, e o gpt-4o tem histórico limpo
    nessa leitura (398 comprovantes, zero ilegíveis). A 2ª chamada (releitura) usa
    gpt-4o e NÃO manda reasoning_effort (parâmetro da família gpt-5)."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(side_effect=[
        _mock_openai_response("COMPROVANTE DE PAGAMENTO: PIX para PSIQUE, valor não visível"),
        _mock_openai_response("100,00"),
    ])
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.media._enhance_for_reread", side_effect=lambda b: b), \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Maria"}), \
         patch("app.google_drive.upload_image", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/rcpt/view"), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": "folder-1"}):
        await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    reread_kwargs = fake_openai.chat.completions.create.await_args_list[1].kwargs
    assert reread_kwargs["model"] == "gpt-4o"
    assert "reasoning_effort" not in reread_kwargs


async def test_reread_enhances_the_image_before_reading():
    """Antes da releitura, a imagem passa por realce (ampliar/contraste) — é aí que
    mora boa parte das falhas de foto de tela. O realce roda no caminho de foto."""
    from app.media import describe_image_bytes
    fake_openai = AsyncMock()
    fake_openai.chat.completions.create = AsyncMock(side_effect=[
        _mock_openai_response("COMPROVANTE DE PAGAMENTO: PIX PSIQUE, valor não visível"),
        _mock_openai_response("100,00"),
    ])
    with patch("app.media._get_openai", return_value=fake_openai), \
         patch("app.media._enhance_for_reread", return_value=b"enhanced-bytes") as mock_enhance, \
         patch("app.database.get_user_by_phone", new_callable=AsyncMock,
               return_value={"patient_name": "Maria"}), \
         patch("app.google_drive.upload_image", new_callable=AsyncMock,
               return_value="https://drive.google.com/file/d/rcpt/view"), \
         patch.dict("os.environ", {"GOOGLE_DRIVE_PAYMENTS_FOLDER_ID": "folder-1"}):
        await describe_image_bytes(b"fake-bytes", phone="5511999999999@s.whatsapp.net")

    mock_enhance.assert_called_once()


def _tiny_png(width=40, height=20) -> bytes:
    """Gera um PNG pequeno de verdade para exercitar o realce."""
    from io import BytesIO
    from PIL import Image
    img = Image.new("RGB", (width, height), (128, 128, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_enhance_for_reread_upscales_small_image():
    """O realce amplia uma imagem pequena (mais pixels ajudam o modelo a ler o
    valor) e devolve bytes válidos de imagem."""
    from io import BytesIO
    from PIL import Image
    from app.media import _enhance_for_reread
    original = _tiny_png(40, 20)
    out = _enhance_for_reread(original)
    assert isinstance(out, bytes) and out
    w, h = Image.open(BytesIO(out)).size
    assert w > 40 and h > 20  # ampliou


def test_enhance_for_reread_never_raises_on_garbage():
    """Nunca lança: entrada inválida devolve os bytes originais (o realce é um
    reforço, não pode derrubar a leitura)."""
    from app.media import _enhance_for_reread
    assert _enhance_for_reread(b"not-an-image") == b"not-an-image"
