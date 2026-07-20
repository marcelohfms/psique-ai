import os
from dotenv import load_dotenv
load_dotenv()

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

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
result = service.spreadsheets().values().get(
    spreadsheetId=spreadsheet_id, range="Pagamentos!A:J"
).execute()
rows = result.get("values", [])
print(f"total rows: {len(rows)}")
for i, row in enumerate(rows):
    joined = " | ".join(row)
    if "Matheus" in joined or "988851971" in joined or "Mayri" in joined:
        print(i, row)
