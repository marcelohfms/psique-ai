import asyncio
from dotenv import load_dotenv
load_dotenv()

APPT = "uvujd8pjg6aha3a7h6rfegnvmc"
JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
DANIELLA_PHONE = "5581991749847"

NEW_SUMMARY = "Orientação de pais — Bento Ramos Valença (com a mãe, Daniella) [Online]"
NEW_DESCRIPTION = (
    "Orientação de pais (Dr. Júlio conduz só com a mãe nesta etapa)\n"
    "Paciente: Bento Ramos Valença\n"
    "Responsável presente: Daniella Ramos (mãe)\n"
    "Médico: Júlio\n"
    "Modalidade: Online\n"
    "Número: 5581991749847"
)

async def main():
    from app.supabase_client import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    c = await get_supabase()
    doc = await c.from_("doctors").select("agenda_id").eq("doctor_id", JULIO_ID).single().execute()
    cal_id = doc.data["agenda_id"]
    print("calendar:", cal_id)

    creds = _credentials()
    svc = build("calendar", "v3", credentials=creds)

    ev = svc.events().get(calendarId=cal_id, eventId=APPT).execute()
    print("ANTES  summary:", ev.get("summary"))
    print("ANTES  desc:", repr(ev.get("description")))
    print("ANTES  start:", ev.get("start"), "end:", ev.get("end"))

    patch = {"summary": NEW_SUMMARY, "description": NEW_DESCRIPTION}
    out = svc.events().patch(calendarId=cal_id, eventId=APPT, body=patch).execute()
    print("\nDEPOIS summary:", out.get("summary"))
    print("DEPOIS desc:", repr(out.get("description")))

asyncio.run(main())
