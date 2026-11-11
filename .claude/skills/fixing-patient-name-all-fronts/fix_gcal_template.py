"""TEMPLATE — Frente 4: corrige o nome do paciente no evento do Google Calendar.

Copie para scripts/_fix_<nome>_gcal_oneoff.py e preencha as constantes.
Troca OLD por NEW no summary ("Consulta — <nome> [Modalidade]") e na linha "Paciente:" da
descrição, via events().patch, preservando médico/modalidade/número/e-mail/notas e o horário.
Dry-run por padrão; --live efetiva.
"""
import asyncio
import sys

import scripts._dnsfix  # noqa: F401
from dotenv import load_dotenv

load_dotenv()

LIVE = "--live" in sys.argv

# --- PREENCHER com o diagnóstico ---
CAL = "dr.juliogouveia@gmail.com"     # doctors.agenda_id do médico da consulta
EVENT_ID = "COLE_O_appointment_id"    # appointments.appointment_id
OLD = "Nome Do Responsavel"
NEW = "Nome Completo Do Paciente"
# ------------------------------------


async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    svc = build("calendar", "v3", credentials=_credentials())
    ev = svc.events().get(calendarId=CAL, eventId=EVENT_ID).execute()
    summary = ev.get("summary", "")
    desc = ev.get("description", "")
    print("summary ANTES:", summary)
    print("description ANTES:\n", desc)

    assert OLD in summary and OLD in desc, "nome antigo não está no evento — aborta"

    new_summary = summary.replace(OLD, NEW)
    new_desc = desc.replace(OLD, NEW)
    print("\nsummary DEPOIS:", new_summary)
    print("description DEPOIS:\n", new_desc)

    if not LIVE:
        print("\n[DRY-RUN] nada alterado. Rode com --live.")
        return

    svc.events().patch(
        calendarId=CAL, eventId=EVENT_ID,
        body={"summary": new_summary, "description": new_desc},
    ).execute()
    check = svc.events().get(calendarId=CAL, eventId=EVENT_ID).execute()
    print("\nOK. summary agora:", check.get("summary"))


asyncio.run(main())
