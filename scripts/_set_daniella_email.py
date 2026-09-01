import asyncio
from dotenv import load_dotenv
load_dotenv()

DANIELLA = "970df18e-268c-4454-b3ce-dc50882c9c6b"
APPT = "uvujd8pjg6aha3a7h6rfegnvmc"
JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
EMAIL = "daniellaramossilva@hotmail.com"

NEW_DESCRIPTION = (
    "Orientação de pais (Dr. Júlio conduz só com a mãe nesta etapa)\n"
    "Paciente: Bento Ramos Valença\n"
    "Responsável presente: Daniella Ramos (mãe)\n"
    "Médico: Júlio\n"
    "Modalidade: Online\n"
    "Número: 5581991749847\n"
    f"E-mail: {EMAIL}"
)

async def main():
    from app.supabase_client import get_supabase
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build

    c = await get_supabase()
    # 1) grava email na ficha
    await c.from_("patients").update({"email": EMAIL}).eq("id", DANIELLA).execute()
    chk = await c.from_("patients").select("name,email").eq("id", DANIELLA).execute()
    print("ficha ->", chk.data)

    # 2) descricao do evento com o email
    doc = await c.from_("doctors").select("agenda_id").eq("doctor_id", JULIO_ID).single().execute()
    cal_id = doc.data["agenda_id"]
    creds = _credentials()
    svc = build("calendar", "v3", credentials=creds)
    out = svc.events().patch(calendarId=cal_id, eventId=APPT, body={"description": NEW_DESCRIPTION}).execute()
    print("evento desc ->", repr(out.get("description")))

asyncio.run(main())
