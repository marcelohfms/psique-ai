from dotenv import load_dotenv
load_dotenv()
import os
from app.google_drive import _credentials
from googleapiclient.discovery import build

creds = _credentials()
service = build("sheets", "v4", credentials=creds)
spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
resp = service.spreadsheets().values().get(
    spreadsheetId=spreadsheet_id, range="A:J"
).execute()
rows = resp.get("values", [])
for i, r in enumerate(rows):
    if any("Matheus" in str(c) for c in r):
        print(i + 1, r)
