import os
from dotenv import load_dotenv
load_dotenv()

from googleapiclient.discovery import build
from app.google_sheets import _credentials

spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
creds = _credentials()
service = build("sheets", "v4", credentials=creds)

result = service.spreadsheets().values().get(
    spreadsheetId=spreadsheet_id, range="A:J"
).execute()
rows = result.get("values", [])
for i, row in enumerate(rows, start=1):
    if "5581997006125" in row or any("5581997006125" in str(c) for c in row):
        print(f"row {i}: {row}")
