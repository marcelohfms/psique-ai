"""One-off: ajusta o Google Calendar para o recesso de fim de ano 2026/2027.

A clínica decidiu atender até quarta 23/12 e retornar:
  - Dra. Bruna: sexta 08/01/2027 (atende seg/qua/sex)
  - Dr. Júlio : segunda 11/01/2027 (atende seg/qua/qui)

Ações:
  1) Abrir o 23/12 — apagar os eventos "🔒 Bloqueado" que estavam nesse dia
     nos dois calendários.
  2) Bloquear os novos dias de recesso em janeiro, criando "🔒 Bloqueado" em
     cada hora da grade, PULANDO qualquer horário que já tenha consulta real
     (ex.: Ricardo José Vieira Cunha Filho, 07/01 14h no calendário do Júlio).

Rodar da worktree com o .env do checkout principal:
  uv run python scripts/recesso_fim_ano_2026_oneoff.py
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv("/Users/ayexatavares/Projetos/psique-ai/.env")

from app.google_calendar import _credentials, TIMEZONE, DOCTOR_SCHEDULES
from googleapiclient.discovery import build

TZ = ZoneInfo(TIMEZONE)
CALS = {
    "julio": "dr.juliogouveia@gmail.com",
    "bruna": "brunalima.psiquiatra@gmail.com",
}

# IDs dos eventos "🔒 Bloqueado" do 23/12 a apagar (abrir o dia).
UNBLOCK_23_12 = {
    "julio": [
        "vdaffsvgpbbk04ius73tu5a9no",  # 09:00
        "1umgdhrn647t9t8lgt04u4lv84",  # 10:00
        "fj176d9r09r30vm5sg0dn191no",  # 11:00
    ],
    "bruna": [
        "26r5k6ckj5etnp61qont92a78g",  # 09:00
        "i3v5pc4gi2js79qtrvgo7cvu54",  # 10:00
        "k5vkon66b4f9hd4kej5ab91ojc",  # 11:00
        "io836op6s4tj7m3luu0hcinfdg",  # 14:00
        "0oongoul3k7afsgl3krqhekk8k",  # 15:00
        "ktti69fe867t5qoh82cur0eq9s",  # 16:00
        "hkdc1f11d929fa368t6lml798c",  # 17:00
    ],
}

# Novos dias de recesso a bloquear (ano, mes, dia).
BLOCK_DAYS = {
    "bruna": [(2027, 1, 1), (2027, 1, 4), (2027, 1, 6)],
    "julio": [(2027, 1, 4), (2027, 1, 6), (2027, 1, 7)],
}


def grade_hours(doctor: str, d: datetime.date) -> list[int]:
    """Horas cheias da grade padrão do medico naquele dia da semana."""
    windows = DOCTOR_SCHEDULES.get(doctor, {}).get(d.weekday(), [])
    hours: list[int] = []
    for sh, sm, eh, em, _mod in windows:
        h = sh
        while h < eh:
            hours.append(h)
            h += 1
    return sorted(set(hours))


def timed_events(service, cal_id, day: datetime.date):
    """Eventos COM horario (nao all-day) no dia."""
    start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=TZ)
    end = start + timedelta(days=1)
    items = service.events().list(
        calendarId=cal_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime",
    ).execute().get("items", [])
    out = []
    for ev in items:
        s = ev.get("start", {}).get("dateTime")
        e = ev.get("end", {}).get("dateTime")
        if not s or not e:
            continue  # all-day → ignora para fins de "ocupado"
        out.append((
            datetime.fromisoformat(s).astimezone(TZ),
            datetime.fromisoformat(e).astimezone(TZ),
            ev.get("summary", "(sem titulo)"),
        ))
    return out


def main():
    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    print("== 1) Abrindo o 23/12 (apagando bloqueios) ==")
    for doctor, ids in UNBLOCK_23_12.items():
        cal = CALS[doctor]
        for ev_id in ids:
            try:
                service.events().delete(calendarId=cal, eventId=ev_id).execute()
                print(f"  [{doctor}] apagado bloqueio {ev_id}")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{doctor}] FALHA ao apagar {ev_id}: {exc}")

    print("\n== 2) Bloqueando dias de recesso em janeiro ==")
    skipped: list[str] = []
    for doctor, days in BLOCK_DAYS.items():
        cal = CALS[doctor]
        for y, m, d in days:
            day = datetime(y, m, d, tzinfo=TZ).date()
            hours = grade_hours(doctor, day)
            busy = timed_events(service, cal, day)
            for hour in hours:
                hstart = datetime(y, m, d, hour, 0, tzinfo=TZ)
                hend = hstart + timedelta(hours=1)
                conflict = next(
                    (b for b in busy if b[0] < hend and b[1] > hstart
                     and "bloqueado" not in b[2].lower()),
                    None,
                )
                if conflict:
                    msg = (f"  [{doctor}] PULADO {d:02d}/{m:02d} {hour:02d}:00 "
                           f"— já ocupado: {conflict[2]}")
                    print(msg)
                    skipped.append(msg.strip())
                    continue
                # se já existe um bloqueio nessa hora, não duplica
                already_blocked = any(
                    b[0] < hend and b[1] > hstart and "bloqueado" in b[2].lower()
                    for b in busy
                )
                if already_blocked:
                    print(f"  [{doctor}] já bloqueado {d:02d}/{m:02d} {hour:02d}:00")
                    continue
                event = {
                    "summary": "🔒 Bloqueado",
                    "description": "Horário bloqueado — recesso de fim de ano.",
                    "start": {"dateTime": hstart.isoformat(), "timeZone": TIMEZONE},
                    "end": {"dateTime": hend.isoformat(), "timeZone": TIMEZONE},
                }
                res = service.events().insert(calendarId=cal, body=event).execute()
                print(f"  [{doctor}] bloqueado {d:02d}/{m:02d} {hour:02d}:00 -> {res['id']}")

    if skipped:
        print("\n== ATENÇÃO — horários NÃO bloqueados (consulta real no dia de recesso) ==")
        for s in skipped:
            print("  " + s)


if __name__ == "__main__":
    main()
