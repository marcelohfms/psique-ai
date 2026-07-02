import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.email_sender import send_clinic_notification_email
    subject = "Consulta cancelada por falta de pagamento — Maria Alice Zacarias De Melo Costa"
    body = (
        "A consulta abaixo foi cancelada automaticamente por falta de pagamento da taxa de reserva.\n\n"
        "Paciente: Maria Alice Zacarias De Melo Costa\n"
        "Responsável: Angélica Magaly Zacarias de Melo Costa\n"
        "Médico(a): Dr. Júlio\n"
        "Data/hora: 22/06/2026 às 10:00\n"
        "WhatsApp: 5587981054742\n\n"
        "A vaga foi liberada no Google Calendar.\n\n"
        "Nota: cancelamento ocorreu às 03:28 de 11/06 por bug no script (fora da janela horária). "
        "Aviso ao paciente enviado manualmente às 13:57 de 11/06. Bug corrigido."
    )
    await send_clinic_notification_email(subject, body)
    print("✅ E-mail enviado para a clínica.")

asyncio.run(main())
