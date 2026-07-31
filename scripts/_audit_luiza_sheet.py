"""Cruza a planilha Pagamentos com as duas Luizas: a paciente de 558191183875
(Luiza Siqueira Barbosa, sem nenhuma consulta no banco) e a Luíza Brito de Melo
Machado, que é quem de fato aparece na agenda do Dr. Júlio em 15/07 às 10h."""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.google_sheets import _credentials
    from googleapiclient.discovery import build

    service = build("sheets", "v4", credentials=_credentials())
    spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range="Pagamentos!A:J"
    ).execute()
    rows = result.get("values", [])
    header, data = rows[0], rows[1:]
    print(" | ".join(header))
    for r in data:
        line = " | ".join(r)
        if "luiza" in line.lower() or "luíza" in line.lower() or "91183875" in line:
            print(line)

asyncio.run(main())
