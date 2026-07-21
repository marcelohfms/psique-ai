import asyncio
from dotenv import load_dotenv
load_dotenv()

CALENDAR_ID = "dr.juliogouveia@gmail.com"
EVENT_ID = "ccc04n5i65dprqvvcrfdjnit18"
DOC_ID = 161

PATIENT_NAME = "Luiz Guilherme da Rocha Carneiro"
MEDICATION_NOTE = "razapina 15mg"


async def main():
    from app.google_calendar import _credentials
    from googleapiclient.discovery import build
    from app.database import get_supabase

    creds = _credentials()
    service = build("calendar", "v3", credentials=creds)

    event = service.events().get(calendarId=CALENDAR_ID, eventId=EVENT_ID).execute()
    print("BEFORE summary:", event.get("summary"))
    print("BEFORE description:", event.get("description"))

    new_summary = f"Consulta — {PATIENT_NAME} [Online]"
    new_description = (
        f"Paciente: {PATIENT_NAME}\n"
        f"Médico: Dr. Júlio\n"
        f"Modalidade: Online\n"
        f"Número: 5581999299797\n"
        f"E-mail: marciamrocha@hotmail.com\n"
        f"Obs: Renovação de receita solicitada (mãe pediu na 1ª mensagem) — {MEDICATION_NOTE}"
    )

    patch = {"summary": new_summary, "description": new_description}
    updated = service.events().patch(calendarId=CALENDAR_ID, eventId=EVENT_ID, body=patch).execute()
    print("\nAFTER summary:", updated.get("summary"))
    print("AFTER description:", updated.get("description"))

    client = await get_supabase()
    doc = await client.from_("documents").select("*").eq("id", DOC_ID).single().execute()
    metadata = doc.data["metadata"]
    print("\nBEFORE documents.metadata:", metadata)

    metadata["patient_name"] = PATIENT_NAME
    metadata["medication_note"] = MEDICATION_NOTE

    result = await client.from_("documents").update({"metadata": metadata}).eq("id", DOC_ID).execute()
    print("\nAFTER documents.metadata:", result.data[0]["metadata"])


asyncio.run(main())
