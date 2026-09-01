import asyncio, os
from dotenv import load_dotenv
load_dotenv()
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

def main():
    creds = Credentials(token=None, refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"], client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    service = build("sheets", "v4", credentials=creds)
    # get hyperlinks via includeGridData for rows 443 and 455, col I (index 8)
    for rownum in (443, 455):
        rng = f"Pagamentos!I{rownum}:I{rownum}"
        resp = service.spreadsheets().get(spreadsheetId=sid, ranges=[rng],
            fields="sheets(data(rowData(values(formattedValue,hyperlink,userEnteredFormat,textFormatRuns))))").execute()
        try:
            cell = resp["sheets"][0]["data"][0]["rowData"][0]["values"][0]
        except Exception:
            cell = {}
        print(f"ROW {rownum} col I: text={cell.get('formattedValue')!r} hyperlink={cell.get('hyperlink')!r}")
        runs = cell.get("textFormatRuns")
        if runs:
            for run in runs:
                link = run.get("format", {}).get("link", {}).get("uri")
                if link:
                    print("    run link:", link)

main()
