from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()
from app.google_calendar import _credentials, TIMEZONE
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)
CAL = "dr.juliogouveia@gmail.com"


def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=TZ).isoformat()
    end = datetime(2026, 12, 31, 23, 59, tzinfo=TZ).isoformat()
    page_token = None
    blocks = []
    while True:
        resp = service.events().list(
            calendarId=CAL, timeMin=start, timeMax=end,
            singleEvents=True, orderBy="startTime",
            q="Bloqueado", pageToken=page_token,
        ).execute()
        for e in resp.get("items", []):
            if "Bloqueado" in e.get("summary", ""):
                blocks.append(e)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"Total eventos '🔒 Bloqueado' no Calendar: {len(blocks)}\n")
    by_date = {}
    for e in blocks:
        s = e["start"].get("dateTime", e["start"].get("date"))
        d = s[:10]
        by_date.setdefault(d, []).append(s[11:16] if "T" in s else "dia inteiro")
    for d in sorted(by_date):
        print(f"  {d}: {sorted(by_date[d])}")


if __name__ == "__main__":
    main()
