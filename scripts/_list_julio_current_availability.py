import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from zoneinfo import ZoneInfo

load_dotenv()

async def main():
    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id

    TZ = ZoneInfo("America/Recife")
    now = datetime.now(TZ)

    calendar_id = await _get_doctor_calendar_id("julio")
    if not calendar_id:
        print("❌ Não foi possível encontrar o calendário do Dr. Júlio")
        return

    print(f"📅 Disponibilidades do Dr. Júlio (a partir de {now.strftime('%d/%m/%Y %H:%M')})")
    print("=" * 70)

    # Check next 4 weeks
    current_date = now.date()
    for week_offset in range(4):
        check_date = current_date + timedelta(weeks=week_offset)

        # Check each weekday
        for days_offset in range(7):
            date_to_check = check_date + timedelta(days=days_offset)

            # Skip past dates
            if date_to_check < current_date:
                continue

            date_str = date_to_check.strftime("%d/%m")
            weekday_num = date_to_check.weekday()
            weekday_names_pt = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
            weekday_name = weekday_names_pt[weekday_num]

            # Get all shifts available
            slots = await get_available_slots(
                calendar_id=calendar_id,
                preferred_day=date_str,
                preferred_shift="qualquer",
                slot_minutes=60,
                doctor_key="julio",
            )

            if slots:
                print(f"\n✅ {weekday_name.upper()}, {date_str}:")
                for start, modality in slots:
                    time_str = start.astimezone(TZ).strftime('%H:%M')
                    modality_label = modality or "escolha"
                    print(f"   • {time_str} [{modality_label}]")
            else:
                print(f"\n❌ {weekday_name.upper()}, {date_str}: sem vagas")

asyncio.run(main())
