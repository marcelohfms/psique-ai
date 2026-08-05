import asyncio
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime
from zoneinfo import ZoneInfo
TZ = ZoneInfo("America/Recife")

async def main():
    import httpx
    from app.chatwoot import _base_url, _account_id, _headers, _request
    url = f"{_base_url()}/api/v1/accounts/{_account_id()}/conversations/370/messages"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await _request(c, "GET", url, headers=_headers())
        data = r.json().get("payload") or {}
        msgs = data if isinstance(data, list) else (data.get("messages") or [])
    print("total:", len(msgs))
    for m in msgs:
        ts = m.get("created_at")
        try: ts = datetime.fromtimestamp(ts, TZ).strftime("%d/%m %H:%M:%S")
        except Exception: pass
        sender = (m.get("sender") or {})
        print(f"[{ts}] type={m.get('message_type')} priv={m.get('private')} sender={sender.get('type')}/{sender.get('name')!r} :: {str(m.get('content'))[:110]!r}")

asyncio.run(main())
