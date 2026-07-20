import asyncio
from dotenv import load_dotenv
load_dotenv()

FILE_ID = "1aFfDzctpC2aGq67AqmgjE5TCwcIOgB6R"

async def main():
    from app.google_drive import _credentials
    from googleapiclient.discovery import build
    creds = _credentials()
    service = build("drive", "v3", credentials=creds)
    meta = service.files().get(fileId=FILE_ID, fields="name,parents").execute()
    print(meta)

asyncio.run(main())
