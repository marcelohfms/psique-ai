"""
One-off: a consulta de 22/07 (appointment_id 0sgvf77354m6p4ho0no9dbk0gs) foi
agendada para Leticia Camara Lima Alves De Souza Pimentel (patient_id
f2be56bb-aa64-476e-8d1a-8d85b3aa861a), mas ficou gravada sob o patient_id da
mãe/contato Natalia (1cee1241-a23b-4db8-b67c-ce0496513ca2) — a consulta de
08/07 é da Natalia mesma e está correta, não mexer nela.

Confirmado pela clínica em 2026-07-28: 22/07 = Leticia, 08/07 = Natalia.

Uso: uv run python scripts/_fix_leticia_appt_patient_id.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

APPOINTMENT_ID = "0sgvf77354m6p4ho0no9dbk0gs"
WRONG_PATIENT_ID = "1cee1241-a23b-4db8-b67c-ce0496513ca2"  # Natalia
CORRECT_PATIENT_ID = "f2be56bb-aa64-476e-8d1a-8d85b3aa861a"  # Leticia


async def main():
    from app.database import get_supabase
    client = await get_supabase()

    before = await client.from_("appointments").select("*").eq("appointment_id", APPOINTMENT_ID).execute()
    assert len(before.data) == 1, f"esperado 1 appointment, achou {len(before.data)}"
    row = before.data[0]
    assert row["patient_id"] == WRONG_PATIENT_ID, f"patient_id inesperado: {row['patient_id']}"
    print("Antes:", {"patient_id": row["patient_id"]})

    await client.from_("appointments").update({
        "patient_id": CORRECT_PATIENT_ID,
    }).eq("appointment_id", APPOINTMENT_ID).execute()

    after = await client.from_("appointments").select("patient_id").eq("appointment_id", APPOINTMENT_ID).execute()
    print("Depois:", after.data[0])
    assert after.data[0]["patient_id"] == CORRECT_PATIENT_ID
    print("✅ appointment 0sgvf77354m6p4ho0no9dbk0gs agora vinculado à Leticia.")


if __name__ == "__main__":
    asyncio.run(main())
