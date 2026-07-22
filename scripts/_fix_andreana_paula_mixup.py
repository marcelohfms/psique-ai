import asyncio
import os
from datetime import date
from dotenv import load_dotenv
load_dotenv()

CONTACT_ID = "0a3f6664-9c13-4bc4-bad4-7183e960cafb"  # phone 5581997006125
WRONG_PATIENT_ID = "649cd502-b9c4-404d-9277-5f52ea1c4037"  # Paula Morgana Quidute Vieira
APPOINTMENT_ID = "fe933f0f-0409-4411-b472-b818c9f78195"
DOCTOR_JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
DRIVE_FILE_ID = "1aFfDzctpC2aGq67AqmgjE5TCwcIOgB6R"
DRIVE_LINK = "https://drive.google.com/file/d/1aFfDzctpC2aGq67AqmgjE5TCwcIOgB6R/view?usp=drivesdk"

PATIENT_NAME = "Andreana Beserra Quidute Sole"
BIRTH_DATE = "29/04/1965"
EMAIL = "andreanaquidute@gmail.com"
CPF = "412.003.144-68"

NEW_FILENAME_NO_EXT = "Andreana_Beserra_Quidute_Sole_27-07-2026_R$100-00"
NEW_FILENAME_SHEET = "Andreana_Beserra_Quidute_Sole_27-07-2026_R$100,00.jpg"


def _calc_age(birth_date_str: str) -> int:
    d, m, y = (int(x) for x in birth_date_str.split("/"))
    today = date(2026, 7, 14)
    age = today.year - y
    if (today.month, today.day) < (m, d):
        age -= 1
    return age


async def main():
    from app.database import get_supabase
    from app.patients import upsert_patient, link_patient_contact

    client = await get_supabase()

    # 1. Create the new patient record for Andreana
    age = _calc_age(BIRTH_DATE)
    patient_id = await upsert_patient({
        "name": PATIENT_NAME,
        "email": EMAIL,
        "birth_date": BIRTH_DATE,
        "age": age,
        "doctor_id": DOCTOR_JULIO_ID,
        "is_returning_patient": True,
        "patient_cpf": CPF,
    })
    print(f"1. patient created: {patient_id}")
    assert patient_id and patient_id != WRONG_PATIENT_ID

    # 2. Link the new patient to the existing contact (same phone), mirroring Paula's own links
    for role in ("agendamento", "consulta", "financeiro"):
        await link_patient_contact(patient_id, CONTACT_ID, role, is_self=True, relationship="self")
    print("2. patient_contacts linked (agendamento/consulta/financeiro, is_self=True)")

    # 3. Move the appointment from Paula's patient_id to Andreana's
    upd = await client.table("appointments").update({"patient_id": patient_id}).eq("id", APPOINTMENT_ID).execute()
    print("3. appointment moved:", upd.data)

    # 4. Rename the Drive receipt file
    from app.google_drive import rename_file
    await rename_file(DRIVE_FILE_ID, NEW_FILENAME_NO_EXT)
    print("4. drive file renamed")

    # 5. Fix the Pagamentos sheet row (found manually as row 279)
    from app.google_sheets import _credentials, _set_hyperlink_cell
    from googleapiclient.discovery import build
    spreadsheet_id = os.environ["GOOGLE_SHEETS_PAYMENTS_ID"]
    creds = _credentials()
    service = build("sheets", "v4", credentials=creds)

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Pagamentos!B279",
        valueInputOption="RAW",
        body={"values": [[PATIENT_NAME]]},
    ).execute()
    print("5a. sheet Paciente column updated")

    _set_hyperlink_cell(service, spreadsheet_id, "Pagamentos!I279:I279", DRIVE_LINK, NEW_FILENAME_SHEET)
    print("5b. sheet Comprovante hyperlink updated")

    print("\nDONE. patient_id =", patient_id)

asyncio.run(main())
