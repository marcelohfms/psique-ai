import asyncio, os, httpx
from dotenv import load_dotenv
load_dotenv()

async def main():
    base = os.getenv("CHATWOOT_BASE_URL", "").rstrip("/")
    account_id = os.getenv("CHATWOOT_ACCOUNT_ID", "1")
    token = os.getenv("CHATWOOT_USER_TOKEN") or os.getenv("CHATWOOT_AGENT_BOT_TOKEN", "")
    headers = {"api_access_token": token}
    # tenta sem DDI
    PHONES = ["5581995302944", "81995302944", "995302944"]

    async with httpx.AsyncClient(timeout=15) as client:
        for phone in PHONES:
            r = await client.get(f"{base}/api/v1/accounts/{account_id}/contacts/search",
                headers=headers, params={"q": phone, "page": 1})
            print(f"q={phone} → status={r.status_code} | {str(r.json())[:300]}")

asyncio.run(main())
