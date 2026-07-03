import asyncio
from dotenv import load_dotenv
load_dotenv()


async def main():
    from app.google_sheets import append_payment_receipt

    # (a) Saldo da consulta de 17/06 — R$450 (valor de maio: total R$550, R$100 já registrado como taxa)
    await append_payment_receipt(
        patient_name="Marina Leal Ribeiro",
        phone="5581988197076",
        doctor_name="Dra. Bruna",
        appointment_dt="17/06/2026 14:00",
        amount="450,00",
        drive_link="",
        payment_type="Consulta (saldo — valor maio/2026)",
        payment_method_override="PIX",
    )
    print("[OK] R$450 saldo da consulta de 17/06 registrado")

    # (b) Taxa de reserva da nova consulta de 08/07 — R$100 (reconhecimento do valor cobrado a mais)
    await append_payment_receipt(
        patient_name="Marina Leal Ribeiro",
        phone="5581988197076",
        doctor_name="Dra. Bruna",
        appointment_dt="08/07/2026 14:00",
        amount="100,00",
        drive_link="",
        payment_type="Taxa de reserva",
        payment_method_override="PIX",
    )
    print("[OK] R$100 taxa de reserva da consulta de 08/07 registrado")


asyncio.run(main())
