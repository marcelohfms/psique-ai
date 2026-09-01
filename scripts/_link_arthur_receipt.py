import os, asyncio
from dotenv import load_dotenv
load_dotenv()
from app.google_sheets import _credentials, _set_hyperlink_cell
from googleapiclient.discovery import build
SID = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
RANGE = "Pagamentos!A533:J533"
LINK = "https://drive.google.com/file/d/15nqcccu5lmNppQOJkglN6dGR3qmDpkIN/view?usp=drivesdk"
NAME = "Arthur_Tenório_Ribeiro_Clark_28-08-2026_R$550-00.pdf"
def main():
    svc = build("sheets","v4",credentials=_credentials())
    # confere a linha antes de escrever
    row = svc.spreadsheets().values().get(spreadsheetId=SID, range=RANGE).execute().get("values",[[]])[0]
    print("ANTES:", row)
    assert "Arthur" in " ".join(row) and "550" in " ".join(row), "linha inesperada!"
    _set_hyperlink_cell(svc, SID, RANGE, LINK, NAME)
    row2 = svc.spreadsheets().values().get(spreadsheetId=SID, range="Pagamentos!I533").execute().get("values",[[]])
    print("DEPOIS coluna I:", row2)
main()
