"""
One-off: envia a mensagem de pós-consulta que nunca foi enviada para
Natalia (5581996332827) — bug em complete_appointments.py tratava
agendamentos marcados no mesmo dia (sem lembrete de véspera, logo sem
confirmed_at) como no-show e pulava o envio, mesmo com presença confirmada
pela conversa (pagamento registrado). Cobre as duas consultas:
  - dela mesma, 08/07
  - da filha Leticia, 22/07
pos_consulta_sent_at já estava marcado nas duas (skip silencioso), então o
cron corrigido não vai reprocessá-las sozinho.

Uso: uv run python scripts/_resend_natalia_pos_consulta.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581996332827"


async def main():
    from scripts.complete_appointments import send_pos_consulta

    for first_name in ("Natalia", "Leticia"):
        print(f"Enviando pós-consulta para {PHONE} ({first_name})...")
        await send_pos_consulta(PHONE, first_name)
        print("✅ Enviado.")


if __name__ == "__main__":
    asyncio.run(main())
