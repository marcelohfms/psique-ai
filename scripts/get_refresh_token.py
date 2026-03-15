"""
Run once to generate the GOOGLE_REFRESH_TOKEN for the .env file.

Usage:
    uv run python scripts/get_refresh_token.py
"""
import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

CLIENT_CONFIG = {
    "installed": {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

SCOPES = ["https://www.googleapis.com/auth/calendar"]

flow = InstalledAppFlow.from_client_config(CLIENT_CONFIG, scopes=SCOPES)
creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")

print("\n✅ Autorização concluída!")
print(f"\nAdicione ao seu .env:\nGOOGLE_REFRESH_TOKEN={creds.refresh_token}")
