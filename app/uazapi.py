import httpx
import os


BASE_URL = os.getenv("UAZAPI_BASE_URL", "https://marceloferro.uazapi.com")


def _headers() -> dict:
    return {
        "token": os.getenv("UAZAPI_TOKEN", ""),
        "Content-Type": "application/json",
    }


async def send_text(phone: str, text: str) -> None:
    """Send a plain text message to a WhatsApp number."""
    # Strip @s.whatsapp.net suffix if present — API expects plain number
    number = phone.replace("@s.whatsapp.net", "")
    payload = {
        "number": number,
        "text": text,
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/send/text",
            json=payload,
            headers=_headers(),
            timeout=10,
        )
        response.raise_for_status()
