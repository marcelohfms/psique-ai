"""Os 5 números que a auditoria apontou com comprovante na conversa e nenhum
evento payment_receipt_registered. Mostra o comprovante e o estado das consultas
para decidir, caso a caso, se falta registrar pagamento."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONES = ["558199480798", "558187639678", "558191183875", "558196980383", "558191812399"]


async def main():
    from app.database import get_supabase
    from app.patients import get_contact_by_phone, get_patients_by_contact
    client = await get_supabase()

    for phone in PHONES:
        print(f"\n{'='*78}\n📞 {phone}")
        msgs = await client.from_("messages").select("*").eq("phone", phone).ilike(
            "content", "%COMPROVANTE DE PAGAMENTO%").order("created_at").execute()
        for m in msgs.data:
            dt = datetime.fromisoformat(m["created_at"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
            print(f"  📄 {dt} | {m['content'][:150]}")

        contact = await get_contact_by_phone(phone)
        if not contact:
            print("  (sem contato cadastrado)")
            continue
        for p in await get_patients_by_contact(contact["id"]):
            appts = await client.from_("appointments").select(
                "start_time, status, booking_fee_paid_at, paid_at, booking_fee_waived"
            ).eq("patient_id", p["id"]).order("start_time", desc=True).limit(4).execute()
            print(f"  👤 {p.get('name')}")
            for a in appts.data:
                st = datetime.fromisoformat(a["start_time"]).astimezone(TZ).strftime("%d/%m/%Y %H:%M")
                print(f"      {st} | {a['status']:10} | taxa={a['booking_fee_paid_at']} "
                      f"| quitado={a['paid_at']} | isento={a['booking_fee_waived']}")

asyncio.run(main())
