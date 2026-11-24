"""Tests for app/google_sheets.py — Pagamentos row + comprovante hyperlink text.

The comprovante cell (column I) shows a filename as clickable text. That text has
to be the name the file ACTUALLY has in Drive: whoever opens the sheet searches
Drive by that name. Before the shared helper existed, the sheet rebuilt its own
version of the name (comma kept, ".jpg" hardcoded) and it never matched.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from app.google_drive import build_receipt_filename
from app.google_sheets import append_payment_receipt, _extend_table_to_row

DRIVE_LINK = "https://drive.google.com/file/d/abc123/view"


def _service_with_table(end_row_index, *, sheet_id=2138324397, start_col=0):
    """Fake Sheets service exposing one native Table on the Pagamentos sheet."""
    rng = {
        "sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": end_row_index,
        "startColumnIndex": start_col, "endColumnIndex": 10,
    }
    meta = {"sheets": [{
        "properties": {"title": "Pagamentos", "sheetId": sheet_id},
        "tables": [{"tableId": "T1", "range": rng}],
    }]}
    service = MagicMock()
    service.spreadsheets.return_value.get.return_value.execute.return_value = meta
    return service


def _batch_update(service):
    return service.spreadsheets.return_value.batchUpdate


@pytest.fixture(autouse=True)
def _payments_sheet_configured():
    old = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    os.environ["GOOGLE_SHEETS_PAYMENTS_ID"] = "sheet-123"
    yield
    if old is None:
        os.environ.pop("GOOGLE_SHEETS_PAYMENTS_ID", None)
    else:
        os.environ["GOOGLE_SHEETS_PAYMENTS_ID"] = old


async def _append(**overrides):
    """Call append_payment_receipt with the Sheets API stubbed out.

    Returns (mock_append_row, mock_set_hyperlink).
    """
    kwargs = {
        "patient_name": "Maria Silva",
        "phone": "5581999999999@s.whatsapp.net",
        "doctor_name": "Dr. Júlio",
        "appointment_dt": "06/07/2026 09:00",
        "amount": "600,00",
        "drive_link": DRIVE_LINK,
    }
    kwargs.update(overrides)
    with patch("app.google_sheets._credentials", MagicMock()), \
         patch("app.google_sheets.build", MagicMock()), \
         patch("app.google_sheets._append_row_payments", MagicMock(return_value="Pagamentos!A5:J5")) as mock_row, \
         patch("app.google_sheets._set_hyperlink_cell", MagicMock()) as mock_link:
        await append_payment_receipt(**kwargs)
    return mock_row, mock_link


def _hyperlink_filename(mock_link) -> str:
    # _set_hyperlink_cell(service, spreadsheet_id, updated_range, drive_link, filename)
    return mock_link.call_args[0][4]


async def test_comprovante_text_is_the_resolved_drive_filename():
    """When the caller already knows the final Drive name (register_payment gets it
    back from rename_file), the sheet must display it verbatim — real extension
    included, so a PDF receipt is not labelled ".jpg"."""
    _, mock_link = await _append(receipt_filename="Maria_Silva_06-07-2026_R$600-00.pdf")
    assert _hyperlink_filename(mock_link) == "Maria_Silva_06-07-2026_R$600-00.pdf"


async def test_comprovante_text_falls_back_to_shared_helper():
    """Callers without a resolved name (one-off scripts, or a failed Drive rename)
    fall back to the same canonical stem the rename would have used — hyphens in
    the amount, and no invented extension."""
    _, mock_link = await _append()
    filename = _hyperlink_filename(mock_link)
    assert filename == build_receipt_filename("Maria Silva", "06/07/2026 09:00", "600,00")
    assert filename == "Maria_Silva_06-07-2026_R$600-00"
    assert "," not in filename
    assert ".jpg" not in filename


async def test_no_hyperlink_written_without_drive_link():
    _, mock_link = await _append(drive_link="")
    mock_link.assert_not_called()


# ── Encaixe da linha na Tabela nativa ─────────────────────────────────────────
# `values().append` grava a linha logo abaixo da Tabela e com o alinhamento padrão
# do Sheets. Ela fica torta de duas formas até alguém abrir a planilha: um degrau
# fora da borda (sem formatação/dropdown) e "descentralizada" (colunas que deviam
# ser centralizadas saem à esquerda/direita). _extend_table_to_row conserta as duas:
# estica a borda e copia o formato da linha de cima para a linha nova.

def _requests(service):
    return _batch_update(service).call_args.kwargs["body"]["requests"]


def _request_types(service):
    return [next(iter(r)) for r in _requests(service)]


def test_extend_table_grows_when_new_row_is_below_the_table():
    service = _service_with_table(end_row_index=581)  # tabela vai até a linha 581
    _extend_table_to_row(service, "sheet-123", "Pagamentos!A583:J583")

    _batch_update(service).assert_called_once()
    assert _request_types(service) == ["updateTable", "copyPaste"]
    req = _requests(service)[0]["updateTable"]
    assert req["table"]["tableId"] == "T1"
    assert req["table"]["range"]["endRowIndex"] == 583  # exclusivo → inclui a linha 583
    assert req["fields"] == "range"


def test_extend_table_reformats_row_already_inside():
    # A linha já está dentro da borda, mas pode ter entrado "descentralizada": ainda
    # precisamos copiar o formato da linha de cima. Não há updateTable, só copyPaste.
    service = _service_with_table(end_row_index=600)
    _extend_table_to_row(service, "sheet-123", "Pagamentos!A583:J583")

    _batch_update(service).assert_called_once()
    assert _request_types(service) == ["copyPaste"]


def test_extend_table_copies_format_from_the_row_above():
    service = _service_with_table(end_row_index=600)
    _extend_table_to_row(service, "sheet-123", "Pagamentos!A583:J583")

    cp = next(r["copyPaste"] for r in _requests(service) if "copyPaste" in r)
    assert cp["pasteType"] == "PASTE_FORMAT"
    # Linha 583 (1-based) = índice 582 (0-based); a fonte é a linha de cima (581 → 582).
    assert cp["source"]["startRowIndex"] == 581 and cp["source"]["endRowIndex"] == 582
    assert cp["destination"]["startRowIndex"] == 582 and cp["destination"]["endRowIndex"] == 583
    assert cp["source"]["startColumnIndex"] == 0 and cp["source"]["endColumnIndex"] == 10


def test_extend_table_noop_on_unparseable_range():
    service = _service_with_table(end_row_index=10)
    _extend_table_to_row(service, "lixo-sem-linha", "")
    _batch_update(service).assert_not_called()


def test_extend_table_ignores_table_not_anchored_on_column_a():
    # Uma tabela que não começa na coluna A não é a de Pagamentos — não mexer.
    service = _service_with_table(end_row_index=100, start_col=3)
    _extend_table_to_row(service, "sheet-123", "Pagamentos!A583:J583")
    _batch_update(service).assert_not_called()


async def test_append_payment_receipt_extends_table_with_appended_range():
    with patch("app.google_sheets._credentials", MagicMock()), \
         patch("app.google_sheets.build", MagicMock()), \
         patch("app.google_sheets._append_row_payments", MagicMock(return_value="Pagamentos!A5:J5")), \
         patch("app.google_sheets._set_hyperlink_cell", MagicMock()), \
         patch("app.google_sheets._extend_table_to_row", MagicMock()) as mock_extend:
        await append_payment_receipt(
            patient_name="Maria Silva",
            phone="5581999999999@s.whatsapp.net",
            doctor_name="Dr. Júlio",
            appointment_dt="06/07/2026 09:00",
            amount="600,00",
            drive_link=DRIVE_LINK,
        )
    mock_extend.assert_called_once()
    # _extend_table_to_row(service, spreadsheet_id, updated_range)
    assert mock_extend.call_args[0][2] == "Pagamentos!A5:J5"
