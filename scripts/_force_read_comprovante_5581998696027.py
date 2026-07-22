"""
Força a leitura do comprovante de pagamento (taxa de reserva) enviado agora por 5581998696027.

Passo 1 (este script): busca a imagem/PDF mais recente na conversa do Chatwoot, roda
o MESMO pipeline de leitura da Eva (describe_image_bytes / describe_pdf_bytes) — que
classifica, faz upload no Drive de pagamentos e extrai o valor — e imprime o resultado.

Uso: uv run python scripts/_force_read_comprovante_5581998696027.py
"""
import asyncio
import datetime
import httpx
from dotenv import load_dotenv

load_dotenv()

PHONE_DIGITS = "5581998696027"
PHONE = "5581998696027@s.whatsapp.net"


async def main():
    from app import chatwoot as cw
    from app.media import describe_image_bytes, describe_pdf_bytes

    async with httpx.AsyncClient(timeout=90) as client:
        contact = await cw._search_contact(client, PHONE_DIGITS)
        if not contact:
            print("❌ Contato não encontrado no Chatwoot.")
            return
        print(f"Contato: id={contact.get('id')} name={contact.get('name')} phone={contact.get('phone_number')}")

        conv = await cw._find_conversation_for_contact(client, contact["id"])
        if not conv:
            print("❌ Conversa não encontrada.")
            return
        conv_id = conv[0]

        url = f"{cw._base_url()}/api/v1/accounts/{cw._account_id()}/conversations/{conv_id}/messages"
        resp = await client.get(url, headers=cw._headers())
        resp.raise_for_status()
        data = resp.json().get("payload") or {}
        messages = data if isinstance(data, list) else (data.get("messages") or [])
        print(f"Total de mensagens na conversa: {len(messages)}")

        # Encontra o anexo (imagem ou pdf) mais recente
        latest = None
        for m in messages:
            for att in (m.get("attachments") or []):
                ft = (att.get("file_type") or "").lower()
                if ft in ("image", "file"):
                    ts = m.get("created_at") or 0
                    if latest is None or ts > latest[0]:
                        latest = (ts, att, m)

        if not latest:
            print("❌ Nenhum anexo (imagem/pdf) encontrado na conversa.")
            return

        ts, att, msg = latest
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        data_url = att.get("data_url") or att.get("thumb_url")
        print(f"\n📎 Anexo mais recente: {dt.isoformat()} type={att.get('file_type')} "
              f"content_type={att.get('content_type')}")
        print(f"   Mensagem: {(msg.get('content') or '')[:200]!r}")
        print(f"   URL: {data_url}")

        # Baixa os bytes
        media_resp = await client.get(data_url, follow_redirects=True)
        media_resp.raise_for_status()
        media_bytes = media_resp.content
        print(f"   Baixado: {len(media_bytes)} bytes")

        # Roda o pipeline REAL de leitura
        ft = (att.get("file_type") or "").lower()
        content_type = (att.get("content_type") or "").lower()
        is_pdf = "pdf" in content_type or (data_url or "").lower().split("?")[0].endswith(".pdf")

        print("\n🔎 Rodando pipeline de leitura da Eva...")
        if ft == "image" and not is_pdf:
            result = await describe_image_bytes(media_bytes, phone=PHONE)
        else:
            result = await describe_pdf_bytes(media_bytes, phone=PHONE)

        print("\n===== RESULTADO DA LEITURA =====")
        print(result)
        print("================================")


if __name__ == "__main__":
    asyncio.run(main())
