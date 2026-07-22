"""One-off: grava manualmente na planilha Pagamentos os 4 pagamentos marcados
pelo painel da atendente (evento attendant_pagamento_registrado) que nunca
chegaram à planilha por causa do bug em dashboard/payments.py::_append_payment_sheet
(falha silenciosa — corrigida em dashboard/payments.py).

Casos (todos confirmados via /audit contra a planilha real em 2026-07-17):
  - Matheus Silva Mônica Lopes | 15/07/2026 17:00 | Consulta R$550 | PIX
  - Natalia Cavalcanti De Britto | 01/07/2026 15:00 | Consulta R$550 | PIX
  - Maria Alice Cavalcanti Cabral De Mello | 30/07/2026 09:00 | Taxa de Reserva R$100 | PIX
  - João Francisco Barcellos de Alencar | 08/07/2026 10:00 | Consulta R$550 | PIX
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

ROWS = [
    # data_registro (agora), paciente, medico, data_consulta, valor, telefone, tipo, forma
    ("Matheus Silva Mônica Lopes", "Dra. Bruna", "15/07/2026 17:00", "550", "5581996746040", "Consulta", "PIX"),
    ("Natalia Cavalcanti De Britto", "Dra. Bruna", "01/07/2026 15:00", "550", "5581999688071", "Consulta", "PIX"),
    ("Maria Alice Cavalcanti Cabral De Mello", "Dr. Júlio", "30/07/2026 09:00", "100", "5581994344760", "Taxa de Reserva", "PIX"),
    ("João Francisco Barcellos de Alencar", "Dra. Bruna", "08/07/2026 10:00", "550", "5581986061977", "Consulta", "PIX"),
]


async def main():
    import os
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    TZ = ZoneInfo("America/Recife")
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    service = build("sheets", "v4", credentials=creds)
    now = datetime.now(TZ).strftime("%d/%m/%Y %H:%M")

    for paciente, medico, data_consulta, valor, phone, tipo, forma in ROWS:
        row = [now, paciente, medico, data_consulta, valor, phone, tipo, forma, "", ""]
        resp = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range="Pagamentos!A:J",
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()
        print("gravado:", paciente, "->", resp.get("updates", {}).get("updatedRange"))


asyncio.run(main())
