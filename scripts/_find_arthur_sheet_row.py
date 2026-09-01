import os, asyncio
from dotenv import load_dotenv
load_dotenv()
from app.google_sheets import _credentials
from googleapiclient.discovery import build
def main():
    sid = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    svc = build("sheets","v4",credentials=_credentials())
    vals = svc.spreadsheets().values().get(spreadsheetId=sid, range="Pagamentos!A:J").execute().get("values",[])
    print(f"total linhas: {len(vals)}")
    print("HEADER:", vals[0] if vals else None)
    for i,row in enumerate(vals, start=1):
        joined = " | ".join(row)
        if "Arthur" in joined or "996503841" in joined:
            print(f"\nLINHA {i}: ", end="")
            for c,letter in zip(row, "ABCDEFGHIJ"):
                print(f"[{letter}]{c}", end="  ")
            print()
main()
