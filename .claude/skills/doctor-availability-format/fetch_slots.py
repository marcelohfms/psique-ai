"""Busca horários disponíveis de um médico em datas específicas.

Uso:
    uv run python .claude/skills/doctor-availability-format/fetch_slots.py \
        --doctor julio --dates 20/07 27/07 [--shift qualquer] [--minutes 60]

Rode a partir da raiz do projeto (para carregar o .env).
Imprime, por data, os horários livres (HH:MM) e a modalidade.
"""
import argparse
import asyncio
from dotenv import load_dotenv

load_dotenv()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor", required=True, help="julio | bruna")
    parser.add_argument("--dates", nargs="+", required=True, help="DD/MM ...")
    parser.add_argument("--shift", default="qualquer", help="manha | tarde | noite | qualquer")
    parser.add_argument("--minutes", type=int, default=60, help="duração do slot em minutos")
    args = parser.parse_args()

    from app.google_calendar import get_available_slots
    from app.graph.tools import _get_doctor_calendar_id
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("America/Recife")
    calendar_id = await _get_doctor_calendar_id(args.doctor)

    for day_str in args.dates:
        slots = await get_available_slots(
            calendar_id=calendar_id,
            preferred_day=day_str,
            preferred_shift=args.shift,
            slot_minutes=args.minutes,
            doctor_key=args.doctor,
        )
        if slots:
            print(f"\n{day_str}:")
            for start, modality in slots:
                print(f"  {start.astimezone(TZ).strftime('%H:%M')} [{modality or 'escolha'}]")
        else:
            print(f"\n{day_str}: sem vagas")


asyncio.run(main())
