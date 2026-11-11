"""TEMPLATE — Frentes 1 e 2: corrige o nome do paciente na planilha Pagamentos e no Drive.

Copie para scripts/_fix_<nome>_sheet_drive_oneoff.py e preencha as constantes.
Troca o nome do responsável (OLD_NAME) pelo nome do paciente (NEW_NAME) na linha ROW da
aba Pagamentos (coluna B = Paciente, coluna I = Comprovante com hyperlink preservado) e
renomeia o arquivo do comprovante no Drive. Dry-run por padrão; --live efetiva.
"""
import asyncio
import os
import sys

import scripts._dnsfix  # noqa: F401
from dotenv import load_dotenv

load_dotenv()

LIVE = "--live" in sys.argv

# --- PREENCHER com o diagnóstico ---
FILE_ID = "COLE_O_ID_DO_ARQUIVO"          # id do drive_link do evento payment_receipt_registered
ROW = 0                                   # linha da aba Pagamentos (1-based)
PHONE = "55819XXXXXXXX"                   # telefone (como está na planilha)
OLD_NAME = "Nome Do Responsavel"          # nome errado que está hoje na linha
NEW_NAME = "Nome Completo Do Paciente"    # patients.name
APPT_DT = "DD/MM/AAAA"                     # data da consulta (para o nome do arquivo)
AMOUNT = "100"                            # valor da linha (ex. "100", "550", "100,00")
# ------------------------------------

LINK = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=drivesdk"
RANGE = f"Pagamentos!A{ROW}:J{ROW}"


async def main():
    from googleapiclient.discovery import build
    from app.google_sheets import _credentials as sheets_creds, _set_hyperlink_cell
    from app.google_drive import build_receipt_filename, rename_file, _drive_file_id

    sid = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    svc = build("sheets", "v4", credentials=sheets_creds())

    row = svc.spreadsheets().values().get(spreadsheetId=sid, range=RANGE).execute().get("values", [[]])[0]
    print("LINHA ANTES:", row)
    joined = " ".join(str(c) for c in row)
    assert OLD_NAME in joined, "nome antigo não está na linha — aborta"
    assert APPT_DT in joined and PHONE in joined, "linha inesperada (data/telefone) — aborta"

    grid = svc.spreadsheets().get(
        spreadsheetId=sid, ranges=[f"Pagamentos!I{ROW}"],
        fields="sheets(data(rowData(values(formattedValue,hyperlink))))",
    ).execute()
    cell = grid["sheets"][0]["data"][0]["rowData"][0]["values"][0]
    assert _drive_file_id(cell.get("hyperlink", "")) == FILE_ID, "hyperlink da coluna I não bate com FILE_ID — aborta"

    new_stem = build_receipt_filename(NEW_NAME, APPT_DT, AMOUNT)
    print("Novo nome (sem extensão):", new_stem)

    if not LIVE:
        print("\n[DRY-RUN] nada alterado. Rode com --live.")
        return

    final_name = await rename_file(FILE_ID, new_stem)   # devolve com a extensão real
    print("Drive renomeado ->", final_name)

    svc.spreadsheets().values().update(
        spreadsheetId=sid, range=f"Pagamentos!B{ROW}",
        valueInputOption="RAW", body={"values": [[NEW_NAME]]},
    ).execute()
    print("Coluna B (Paciente) ->", NEW_NAME)

    _set_hyperlink_cell(svc, sid, RANGE, LINK, final_name)
    print("LINHA DEPOIS:", svc.spreadsheets().values().get(spreadsheetId=sid, range=RANGE).execute().get("values", [[]]))


asyncio.run(main())
