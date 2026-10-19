"""Rede de segurança contra comprovante mudo. Roda a cada 15 min via app/scheduler.py.

Lista os anexos recebidos no Chatwoot na janela, cruza com o nosso banco e, para todo
comprovante ou documento sem rastro (provável webhook da Meta que não chegou), manda
e-mail à clínica tratar pelo painel. A lógica está em app/receipt_reconciliation.py.
"""
import asyncio
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from app.receipt_reconciliation import reconcile

TZ = ZoneInfo("America/Recife")


async def main() -> None:
    try:
        n = await reconcile(datetime.now(TZ))
        print(f"reconcile_dropped_receipts: {n} alerta(s) enviado(s)")
    except Exception:
        print("reconcile_dropped_receipts FALHOU:")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
