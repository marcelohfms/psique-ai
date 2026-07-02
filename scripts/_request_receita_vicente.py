import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.database import get_supabase
    from app.google_sheets import append_document_request
    from app.email_sender import send_document_request_email
    from app.graph.tools import _notify_clinic

    PHONE = "558181963813@s.whatsapp.net"
    PATIENT_NAME = "Vicente Ximênes Lopes Novaes Gonçalves"
    DOCTOR_KEY = "julio"
    DOCTOR_LABEL = "Dr. Júlio"
    DOCTOR_EMAIL = "dr.juliogouveia@gmail.com"

    # Busca dados do paciente
    client = await get_supabase()
    u = await client.from_("users").select("age, email, patient_cpf") \
        .eq("id", "aaaf8986-06fa-4271-9be1-644f198dfe47").single().execute()
    age = u.data.get("age")
    patient_email = u.data.get("email") or ""
    patient_cpf = u.data.get("patient_cpf") or ""

    # Planilha de documentos
    try:
        await append_document_request(
            patient_name=PATIENT_NAME,
            patient_age=age,
            phone=PHONE,
            patient_email=patient_email,
            document_type="receita",
            doctor_name=DOCTOR_LABEL,
            patient_cpf=patient_cpf,
        )
        print("✅ Solicitação registrada na planilha")
    except Exception as e:
        print(f"⚠️  Planilha: {e}")

    # Notifica médico por e-mail
    try:
        await send_document_request_email(
            DOCTOR_KEY, DOCTOR_EMAIL, PATIENT_NAME, age,
            PHONE, patient_email, "receita"
        )
        print("✅ Médico notificado por e-mail")
    except Exception as e:
        print(f"⚠️  E-mail médico: {e}")

    # Notifica clínica via WhatsApp
    await _notify_clinic(
        f"📄 Solicitação de Receita\n"
        f"Paciente: {PATIENT_NAME}\n"
        f"Médico(a): {DOCTOR_LABEL}\n"
        f"Telefone: 558181963813\n"
        f"Solicitado por: Sandra Karoline Ximenes",
        phone=PHONE,
        subject=f"Solicitação de receita — {PATIENT_NAME}",
    )
    print("✅ Clínica notificada")

asyncio.run(main())
