"""
One-off: corrige o hyperlink do comprovante na planilha para o pagamento da Lara (26/05).
Busca a última linha da Lara na aba Pagamentos e aplica o link renomeado.
Uso: uv run python scripts/fix_lara_payment_hyperlink_oneoff.py
"""
import os
import re
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

DRIVE_LINK = "https://drive.google.com/file/d/17hgvdmZjMlA7jmmoxA55Nub5IMa3fIBD/view?usp=drive_link"
PATIENT_NAME = "Lara Pinto de Carvalho"
FILENAME = "Lara_Pinto_de_Carvalho_26-05-2026_R$100,00.jpg"
SPREADSHEET_ID = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")


def _credentials():
    from app.google_sheets import _credentials as base_creds
    return base_creds()


def main():
    if not SPREADSHEET_ID:
        print("❌ GOOGLE_SHEETS_PAYMENTS_ID não configurado.")
        return

    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)

    # Lê todas as linhas da aba Pagamentos
    result = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Pagamentos!A:J",
    ).execute()
    rows = result.get("values", [])

    # Encontra a última linha da Lara
    target_row_index = None  # 0-based
    for i, row in enumerate(rows):
        if len(row) > 1 and PATIENT_NAME.lower() in row[1].lower():
            target_row_index = i

    if target_row_index is None:
        print(f"❌ Linha com '{PATIENT_NAME}' não encontrada na planilha.")
        return

    row_number = target_row_index + 1  # 1-based
    print(f"Linha encontrada: {row_number} → {rows[target_row_index][:5]}")

    # Busca o sheet ID da aba Pagamentos
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, fields="sheets(properties)"
    ).execute()
    sheet_id = next(
        (s["properties"]["sheetId"] for s in meta.get("sheets", [])
         if s["properties"]["title"] == "Pagamentos"),
        None,
    )
    if sheet_id is None:
        print("❌ Aba 'Pagamentos' não encontrada.")
        return

    # Aplica o hyperlink na coluna I (index 8)
    service.spreadsheets().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [{
                "updateCells": {
                    "rows": [{
                        "values": [{
                            "userEnteredValue": {"stringValue": FILENAME},
                            "textFormatRuns": [{
                                "startIndex": 0,
                                "format": {"link": {"uri": DRIVE_LINK}},
                            }],
                        }]
                    }],
                    "fields": "userEnteredValue,textFormatRuns",
                    "start": {
                        "sheetId": sheet_id,
                        "rowIndex": row_number - 1,  # 0-based
                        "columnIndex": 8,  # coluna I
                    },
                }
            }]
        },
    ).execute()

    print(f"✅ Hyperlink aplicado na linha {row_number}, coluna I: '{FILENAME}' → {DRIVE_LINK}")


if __name__ == "__main__":
    main()
