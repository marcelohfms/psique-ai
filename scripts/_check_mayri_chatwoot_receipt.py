import asyncio
import httpx
from dotenv import load_dotenv
load_dotenv()

PHONE_DIGITS = "5581988851971"


async def main():
    from app import chatwoot as cw

    async with httpx.AsyncClient(timeout=15) as client:
        contact = await cw._search_contact(client, PHONE_DIGITS)
        if not contact:
            print("Contato não encontrado no Chatwoot.")
            return
        print(f"Contato Chatwoot: id={contact.get('id')} name={contact.get('name')} phone={contact.get('phone_number')}")

        conv = await cw._find_conversation_for_contact(client, contact["id"])
        print(f"Conversa: {conv}")
        if not conv:
            return
        conv_id = conv[0]

        url = f"{cw._base_url()}/api/v1/accounts/{cw._account_id()}/conversations/{conv_id}/messages"
        resp = await client.get(url, headers=cw._headers())
        resp.raise_for_status()
        data = resp.json().get("payload") or {}
        messages = data if isinstance(data, list) else (data.get("messages") or [])
        print(f"\nTotal de mensagens na conversa: {len(messages)}")

        # today = 2026-07-07, look for messages with attachments, and around 20:00-22:00 UTC
        import datetime
        for m in messages:
            ts = m.get("created_at")
            if not ts:
                continue
            dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            if dt.date() != datetime.date(2026, 7, 7):
                continue
            atts = m.get("attachments") or []
            content = (m.get("content") or "")[:200]
            print(f"  [{dt.isoformat()}] type={m.get('message_type')} private={m.get('private')} sender={m.get('sender', {}).get('name') if m.get('sender') else None} content={content!r} attachments={[a.get('data_url') for a in atts]}")

asyncio.run(main())
