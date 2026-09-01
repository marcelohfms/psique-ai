import asyncio
from dotenv import load_dotenv
load_dotenv()
from app.google_drive import build_receipt_filename, rename_file
FILE_ID = "15nqcccu5lmNppQOJkglN6dGR3qmDpkIN"
async def main():
    stem = build_receipt_filename("Arthur Tenório Ribeiro Clark", "28/08/2026 10:00", "550,00")
    print("stem:", stem)
    try:
        final = await rename_file(FILE_ID, stem)
        print("OK renamed ->", final)
    except Exception as e:
        print("FALHOU rename via bot:", type(e).__name__, e)
asyncio.run(main())
