import asyncio, os
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.google_sheets import _credentials
    from googleapiclient.discovery import build
    loop = asyncio.get_running_loop()
    sid = os.environ.get("GOOGLE_SHEETS_PAYMENTS_ID")
    def read():
        svc = build("sheets","v4",credentials=_credentials())
        return svc.spreadsheets().values().get(spreadsheetId=sid, range="Pagamentos!A:J").execute()
    res = await loop.run_in_executor(None, read)
    rows = res.get("values", [])
    print(f"total rows: {len(rows)}")
    print("HEADER:", rows[0] if rows else None)
    for i,r in enumerate(rows):
        joined = " | ".join(r)
        if "Lara" in joined or "998696027" in joined:
            print(f"  row{i}:", joined)

asyncio.run(main())
