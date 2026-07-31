import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.google_sheets import append_payment_receipt
    from app.database import get_supabase
    
    patient_name = "Bernardo Lima Beltrão Teixeira"
    patient_phone = "5581987415206@s.whatsapp.net"
    doctor_label = "Dr. Júlio"
    appointment_dt = "13/08/2026 15:00"
    amount = "100,00"
    drive_link = "https://drive.google.com/file/d/1D6U40KMVf54MAjjHFOxZHd9ExQp_fiqG/view?usp=drivesdk"
    
    print(f"📝 Registrando na planilha Pagamentos...")
    print(f"   Paciente: {patient_name}")
    print(f"   Médico: {doctor_label}")
    print(f"   Consulta: {appointment_dt}")
    print(f"   Taxa: R$ {amount}")
    
    try:
        await append_payment_receipt(
            patient_name, 
            patient_phone, 
            doctor_label, 
            appointment_dt,
            amount, 
            drive_link, 
            payment_type="Taxa de Reserva",
            payment_method_override="PIX"
        )
        print("✅ Registrado na planilha com sucesso!")
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
