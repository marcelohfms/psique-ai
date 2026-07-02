import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    client = await get_supabase()
    now = datetime.now(ZoneInfo("America/Recife")).isoformat()
    await client.from_("appointments").update({
        "booking_fee_paid_at": now,
    }).eq("appointment_id", "rj02m9ar2ffi1t181tluuk293k").execute()
    print("✅ booking_fee_paid_at registrado")

asyncio.run(main())
