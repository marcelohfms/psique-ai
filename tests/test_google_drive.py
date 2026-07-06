"""Tests for app/google_drive.py — Drive rename extension-preservation logic."""
from unittest.mock import MagicMock

from app.google_drive import _rename_file


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
