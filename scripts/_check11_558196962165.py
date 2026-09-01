import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def main():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sid = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    service = build("sheets", "v4", credentials=creds)
    vals = service.spreadsheets().values().get(spreadsheetId=sid, range="Pagamentos!A:J").execute().get("values", [])
    print("total rows:", len(vals))
    for i, r in enumerate(vals, start=1):
        joined = " | ".join(r)
        if "Suzi" in joined or "Laila" in joined or "6962165" in joined:
            print(f"ROW {i}: {joined}")

main()
