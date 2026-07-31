import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")

async def main():
    from app.google_drive import rename_file
    
    # Extrair file_id do link
    drive_link = "https://drive.google.com/file/d/1D6U40KMVf54MAjjHFOxZHd9ExQp_fiqG/view?usp=drivesdk"
    import re
    match = re.search(r'/d/([^/?]+)', drive_link)
    if not match:
        print("❌ Não consegui extrair o file_id do link")
        return
    
    file_id = match.group(1)
    print(f"File ID: {file_id}")
    
    # Formato: {patient_name}_{DD-MM-AAAA}_R${valor}
    patient_name = "Bernardo_Lima_Beltrão_Teixeira"
    date_str = "31-07-2026"  # data do comprovante
    amount = "100-00"
    new_filename = f"{patient_name}_{date_str}_R${amount}"
    
    print(f"📝 Renomeando no Drive para: {new_filename}")
    try:
        await rename_file(file_id, new_filename)
        print("✅ Arquivo renomeado com sucesso!")
    except Exception as e:
        print(f"⚠️  Erro ao renomear: {e}")
        print("   (O link ainda funciona, apenas o nome do arquivo não foi atualizado)")

asyncio.run(main())
