"""Tests for app/google_drive.py — Drive rename extension-preservation logic."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.google_drive import (
    TZ,
    _rename_file,
    _share_with_clinic,
    _upload_and_share,
    build_receipt_filename,
    rename_file,
)


def _mock_service(current_name: str):
    service = MagicMock()
    service.files.return_value.get.return_value.execute.return_value = {"name": current_name}
    return service


def test_rename_file_preserves_pdf_extension():
    """new_name is passed without an extension; the file's actual current
    extension (pdf here) must be reused instead of assuming jpg."""
    service = _mock_service("comprovante_paciente_20260706_120000.pdf")
    _rename_file(service, "file-id-1", "Maria_Silva_06-07-2026_R$100-00")

    update_call = service.files.return_value.update.call_args
    assert update_call.kwargs["fileId"] == "file-id-1"
    assert update_call.kwargs["body"]["name"] == "Maria_Silva_06-07-2026_R$100-00.pdf"


def test_rename_file_preserves_jpg_extension():
    service = _mock_service("comprovante_paciente_20260706_120000.jpg")
    _rename_file(service, "file-id-2", "Joao_Souza_06-07-2026_R$100-00")

    update_call = service.files.return_value.update.call_args
    assert update_call.kwargs["body"]["name"] == "Joao_Souza_06-07-2026_R$100-00.jpg"


def test_rename_file_no_current_extension_uses_new_name_as_is():
    service = _mock_service("some-file-without-extension")
    _rename_file(service, "file-id-3", "Ana_Costa_06-07-2026_R$100-00")

    update_call = service.files.return_value.update.call_args
    assert update_call.kwargs["body"]["name"] == "Ana_Costa_06-07-2026_R$100-00"


# ── final name is reported back to callers ────────────────────────────────────
# The extension is only known here (it's read from Drive), so the resolved name
# has to travel back out — the Pagamentos sheet displays it as the comprovante
# hyperlink text and must match the file byte for byte.

def test_rename_file_returns_final_name_with_extension():
    service = _mock_service("comprovante_paciente_20260706_120000.pdf")
    final = _rename_file(service, "file-id-1", "Maria_Silva_06-07-2026_R$100-00")
    assert final == "Maria_Silva_06-07-2026_R$100-00.pdf"


def test_rename_file_returns_name_without_extension_when_file_had_none():
    service = _mock_service("some-file-without-extension")
    final = _rename_file(service, "file-id-3", "Ana_Costa_06-07-2026_R$100-00")
    assert final == "Ana_Costa_06-07-2026_R$100-00"


async def test_async_rename_file_returns_final_name():
    with patch("app.google_drive._credentials"), \
         patch("app.google_drive.build"), \
         patch("app.google_drive._rename_file", return_value="Maria_Silva_06-07-2026_R$100-00.pdf"):
        final = await rename_file("file-id-1", "Maria_Silva_06-07-2026_R$100-00")
    assert final == "Maria_Silva_06-07-2026_R$100-00.pdf"


# ── build_receipt_filename (shared by the Drive rename and the sheet) ─────────

def test_build_receipt_filename_uses_hyphens_in_amount_and_no_extension():
    name = build_receipt_filename("Maria Silva", "06/07/2026 09:00", "R$ 600,00")
    assert name == "Maria_Silva_06-07-2026_R$600-00"


def test_build_receipt_filename_placeholder_when_amount_unknown():
    name = build_receipt_filename("Maria Silva", "06/07/2026 09:00", "?")
    assert name == "Maria_Silva_06-07-2026_R$valor-nao-identificado"


def test_build_receipt_filename_falls_back_to_today_without_appointment():
    today = datetime.now(TZ).strftime("%d-%m-%Y")
    assert build_receipt_filename("Maria Silva", "—", "600,00") == f"Maria_Silva_{today}_R$600-00"
    assert build_receipt_filename("Maria Silva", "", "600,00") == f"Maria_Silva_{today}_R$600-00"


# ── Compartilhamento restrito (fecha o link público "anyone") ─────────────────

def _upload_service():
    service = MagicMock()
    service.files.return_value.create.return_value.execute.return_value = {
        "id": "file-xyz", "webViewLink": "https://drive.google.com/file/d/file-xyz/view",
    }
    return service


def test_share_with_clinic_usa_por_usuario_e_nunca_publico(monkeypatch):
    monkeypatch.setenv("DRIVE_SHARE_EMAILS", "a@clinica.com, b@clinica.com")
    service = MagicMock()
    _share_with_clinic(service, "file-xyz")

    calls = service.permissions.return_value.create.call_args_list
    assert len(calls) == 2
    emails = {c.kwargs["body"]["emailAddress"] for c in calls}
    assert emails == {"a@clinica.com", "b@clinica.com"}
    for c in calls:
        assert c.kwargs["body"]["type"] == "user"
        assert c.kwargs["body"]["role"] == "reader"
        assert c.kwargs["body"].get("type") != "anyone"
        assert c.kwargs["sendNotificationEmail"] is False


def test_share_with_clinic_sem_emails_nao_compartilha(monkeypatch):
    """Falha fechado: sem a lista, o arquivo fica só com a conta dona, nunca público."""
    monkeypatch.delenv("DRIVE_SHARE_EMAILS", raising=False)
    service = MagicMock()
    _share_with_clinic(service, "file-xyz")
    service.permissions.return_value.create.assert_not_called()


def test_upload_and_share_nunca_torna_publico(monkeypatch):
    monkeypatch.setenv("DRIVE_SHARE_EMAILS", "clinica@exemplo.com")
    service = _upload_service()
    link = _upload_and_share(service, "folder-1", "arquivo.pdf", b"bytes", "application/pdf")
    assert link == "https://drive.google.com/file/d/file-xyz/view"
    calls = service.permissions.return_value.create.call_args_list
    assert all(c.kwargs["body"]["type"] != "anyone" for c in calls)
    assert calls[0].kwargs["body"]["emailAddress"] == "clinica@exemplo.com"
