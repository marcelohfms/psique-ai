"""Backfill: espelhar o comprovante da taxa de reserva de Isaac Caleb Silva Amaral
no histórico da conversa, no mesmo formato que dashboard/payments.mark_paid() agora
grava automaticamente (ver commit que adiciona simetria com find_receipts).

Este caso é anterior ao fix — o pagamento foi registrado via dashboard antes da
mensagem espelho existir, então o comprovante nunca apareceu no histórico nem em
find_receipts. Este script insere a mesma linha retroativamente.
"""
import asyncio

PATIENT_PHONE = "5581987385089"  # contato Elisabete Da silva, quem agendou por Isaac
PAYMENT_TYPE = "Taxa de Reserva"
AMOUNT = "150,00"
DRIVE_LINK = "https://drive.google.com/file/d/1YzpYTPjiPYqTVj8B4C5VP4xLC_rl4cSH/view?usp=drive_link"


async def main():
    from app.database import get_supabase

    client = await get_supabase()

    # Safety: don't duplicate if a receipt message already exists for this phone.
    existing = await client.table("messages").select("content").eq("phone", PATIENT_PHONE).execute()
    if any("drive_link" in (r.get("content") or "") for r in existing.data or []):
        print("Já existe mensagem de comprovante para este telefone — abortando para evitar duplicidade.")
        return

    content = (
        f"[imagem]: COMPROVANTE DE PAGAMENTO: {PAYMENT_TYPE} R$ {AMOUNT} "
        f"— registrado pela atendente [drive_link:{DRIVE_LINK}]"
    )
    await client.from_("messages").insert({
        "phone": PATIENT_PHONE,
        "role": "user",
        "content": content,
    }).execute()
    print("Mensagem de comprovante inserida retroativamente:")
    print(content)


if __name__ == "__main__":
    import os
    from pathlib import Path
    for line in Path(".env").read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v.strip("'\"")
    asyncio.run(main())
