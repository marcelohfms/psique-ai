"""TEMPLATE de correção manual de comprovante perdido (ver SKILL.md da skill
recovering-lost-receipt). Copie para scripts/_fix_<nome>_<telefone>_taxa_oneoff.py
(scripts/_*.py é ignorado pelo git), preencha o bloco de CONSTANTES e rode:

  uv run python scripts/_fix_...py                 # DRY RUN: só lê e mostra o que faria
  APPLY=1 uv run python scripts/_fix_...py         # escreve tudo, sem WhatsApp
  APPLY=1 DO_SEND=1 uv run python scripts/_fix_...py   # escreve + avisa o contato
  NEW_START="2026-10-28T15:00"  (Recife) sobrescreve o horário, se a clínica escolher outro.

Caso real que originou o template: Tarso/Amanda, 25/10/2026 (memória
caso-tarso-amanda-comprovante-dropado-cancelado).

Cobre o CASO B (consulta cancelada pelo cron unpaid_booking_fee): recria o evento no GCal e
REATIVA a mesma linha de appointments. Para o CASO A (consulta ainda scheduled) apague o
bloco "1. GCal + reativação" e troque a guarda de status para `!= "scheduled"`.
"""
import asyncio, os, re, hashlib, sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/Users/ayexatavares/Projetos/psique-ai")
import scripts._dnsfix  # noqa: F401
import httpx
from dotenv import load_dotenv

load_dotenv("/Users/ayexatavares/Projetos/psique-ai/.env")

from app.supabase_client import get_supabase
from app.database import log_event
from app.google_calendar import TZ, create_event, _credentials, _get_busy
from app.google_drive import upload_image, rename_file, build_receipt_filename
from app.google_sheets import append_payment_receipt
from app.email_sender import send_clinic_notification_email
from app.whatsapp import send_text
from app.chatwoot import add_private_note, send_template_message, last_incoming_at
from googleapiclient.discovery import build as _build

# ── CONSTANTES: preencha com o diagnóstico (ver SKILL.md, seção Diagnóstico) ──
PHONE = "55819XXXXXXXX"            # dígitos, com o 9
THREAD = f"{PHONE}@s.whatsapp.net"
CW_CONV = 0                        # id da conversa no Chatwoot
CW_ATTACHMENT_ID = 0               # id do anexo (attachments[].id) do comprovante
EXPECTED_MD5 = ""                  # md5 do anexo, conferido no diagnóstico

APPT_ROW_ID = ""                   # appointments.id (uuid) da consulta cancelada/aberta
OLD_EVENT_ID = ""                  # appointments.appointment_id atual (id do GCal)
PATIENT_ID = ""
PATIENT_NAME = ""                  # patients.name (nunca contacts.name)
PATIENT_EMAIL = ""
DOCTOR_ID = ""
DOCTOR_NAME = "Dra. Bruna"         # "Dr. Júlio" | "Dra. Bruna"
CALENDAR_ID = ""                   # doctors.agenda_id
MODALITY = "presencial"            # appointments.modality
SLOT_MINUTES = 60                  # 60 adulto; 120 menor na 1ª consulta
CONTACT_FIRST = ""                 # primeiro nome de quem conversa (do Chatwoot, contacts.name costuma ser null)
PAYER = ""                         # nome do pagador lido no comprovante

AMOUNT = "100,00"
PAYMENT_TYPE = "Taxa de Reserva"
PAID_AT = "2026-01-01T00:00:00+00:00"  # hora do envio do comprovante, em UTC (Recife = UTC-3)

NEW_START_LOCAL = os.getenv("NEW_START", "2026-01-01T00:00")  # horário Recife confirmado pela Ayexa
APPLY = os.getenv("APPLY") == "1"
DO_SEND = os.getenv("DO_SEND") == "1"


def _drive_file_id(link: str) -> str:
    m = re.search(r"/d/([A-Za-z0-9_-]+)", link or "")
    return m.group(1) if m else ""


def _fmt(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M")


async def main():
    client = await get_supabase()
    start = datetime.fromisoformat(NEW_START_LOCAL).replace(tzinfo=TZ)
    end = start + timedelta(minutes=SLOT_MINUTES)
    appt_dt = _fmt(start)
    print(f"modo: {'APPLY' if APPLY else 'DRY RUN'} | DO_SEND={DO_SEND} | novo horário: {appt_dt} ({start.strftime('%A')})")

    # ── Guardas ───────────────────────────────────────────────────────────────
    appt = (await client.from_("appointments").select(
        "id, status, appointment_id, start_time, booking_fee_paid_at, patient_id, contact_id, doctor_id, modality"
    ).eq("id", APPT_ROW_ID).single().execute()).data
    print("appt antes:", appt)
    if appt["status"] != "canceled" or appt.get("booking_fee_paid_at"):
        print("ABORT: linha já não está 'canceled' ou já tem booking_fee_paid_at — provavelmente já rodou.")
        return
    if appt["appointment_id"] != OLD_EVENT_ID:
        print("ABORT: appointment_id difere do esperado:", appt["appointment_id"])
        return

    active = (await client.from_("appointments").select("id, status, start_time").eq(
        "patient_id", PATIENT_ID).in_("status", ["scheduled", "pending_reschedule"]).execute()).data
    if active:
        print("ABORT: paciente já tem consulta ativa:", active)
        return

    dup = (await client.from_("events").select("id, phone, created_at").eq(
        "event_type", "payment_receipt_registered").contains("metadata", {"file_md5": EXPECTED_MD5}).execute()).data
    if dup:
        print("ABORT: md5 do comprovante já registrado em events:", dup)
        return

    svc = _build("calendar", "v3", credentials=_credentials(), cache_discovery=False)
    loop = asyncio.get_running_loop()
    busy = await loop.run_in_executor(None, _get_busy, svc, CALENDAR_ID, start, end)
    if busy:
        print("ABORT: horário ocupado no GCal do médico:", busy)
        return
    print("GCal: horário livre ✔")

    # ── Comprovante (Chatwoot) ────────────────────────────────────────────────
    b = os.environ["CHATWOOT_BASE_URL"].rstrip("/")
    a = os.environ["CHATWOOT_ACCOUNT_ID"]
    t = os.environ["CHATWOOT_USER_TOKEN"]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"api_access_token": t}) as hc:
        msgs = (await hc.get(f"{b}/api/v1/accounts/{a}/conversations/{CW_CONV}/messages")).json()["payload"]
        data_url = None
        for m in msgs:
            for att in (m.get("attachments") or []):
                if att.get("id") == CW_ATTACHMENT_ID:
                    data_url = att.get("data_url")
        assert data_url, f"anexo {CW_ATTACHMENT_ID} não encontrado na conversa {CW_CONV}"
        img = (await hc.get(data_url)).content
    md5 = hashlib.md5(img).hexdigest()
    print(f"comprovante baixado: {len(img)} bytes md5={md5}")
    if md5 != EXPECTED_MD5:
        print("ABORT: md5 baixado difere do esperado.")
        return

    # formato do phone usado em messages para este contato (digits ou thread)
    prev = (await client.from_("messages").select("phone").in_("phone", [PHONE, THREAD])
            .order("created_at", desc=True).limit(1).execute()).data
    msg_phone = prev[0]["phone"] if prev else THREAD
    print("messages.phone será:", msg_phone)

    last_in = await last_incoming_at(PHONE)
    window_open = bool(last_in) and (datetime.now(timezone.utc).timestamp() - last_in) < 24 * 3600
    print("última msg do contato (epoch):", last_in, "| janela 24h aberta:", window_open)

    patient_text = (
        f"Olá, {CONTACT_FIRST}! 😊\n"
        f"Passando apenas para confirmar o recebimento de R$ {AMOUNT} referente à sua taxa de reserva. "
        f"Não se preocupe, a consulta de {PATIENT_NAME.split()[0]} com {DOCTOR_NAME} está garantida para "
        f"{start.strftime('%d/%m')} às {start.strftime('%H:%M')} ({MODALITY}). Muito obrigada! 💜"
    )
    template_text = (
        f"Olá, {CONTACT_FIRST}! 😊\n"
        f"Passando apenas para confirmar o recebimento de R$ {AMOUNT} referente à "
        f"sua taxa de reserva. Não se preocupe, a consulta de {PATIENT_NAME.split()[0]} está garantida. Muito obrigada! 💜"
    )

    if not APPLY:
        print("\nDRY RUN — nada escrito. Mensagem que iria ao contato:")
        print("   >>>", patient_text if window_open else f"[template pagamento_taxa_recebido] {template_text}")
        return

    # ── 1. GCal + reativação da linha ─────────────────────────────────────────
    new_event_id = await create_event(
        CALENDAR_ID, start, SLOT_MINUTES, PATIENT_NAME, DOCTOR_NAME.replace("Dr. ", "").replace("Dra. ", ""),
        modality=MODALITY, patient_email=PATIENT_EMAIL, patient_number=PHONE,
    )
    print("GCal: evento criado", new_event_id)
    now_iso = datetime.now(TZ).isoformat()
    await client.from_("appointments").update({
        "status": "scheduled",
        "appointment_id": new_event_id,
        "start_time": start.astimezone(timezone.utc).isoformat(),
        "end_time": end.astimezone(timezone.utc).isoformat(),
        "booking_fee_paid_at": PAID_AT,
        "payment_reminder_sent_at": None,
        "updated_at": now_iso,
    }).eq("id", APPT_ROW_ID).execute()
    print("appointments: linha reativada (scheduled, booking_fee_paid_at =", PAID_AT, ")")
    await log_event("appointment_reactivated_manual", PHONE, {
        "patient_name": PATIENT_NAME, "appointment_row_id": APPT_ROW_ID,
        "old_appointment_id": OLD_EVENT_ID, "new_appointment_id": new_event_id,
        "start_time": start.isoformat(), "source": "manual_oneoff_script",
        "reason": "cancelada pelo cron unpaid_booking_fee apos comprovante dropado na ingestao",
    })

    # ── 2. Drive + planilha + evento + espelho + e-mail ───────────────────────
    tmp_name = build_receipt_filename(PATIENT_NAME, appt_dt, AMOUNT)
    drive_link = await upload_image(img, f"{tmp_name}.jpg", "image/jpeg")
    file_id = _drive_file_id(drive_link)
    final_filename = await rename_file(file_id, tmp_name) if file_id else f"{tmp_name}.jpg"
    print("Drive:", drive_link, "|", final_filename)

    await append_payment_receipt(
        patient_name=PATIENT_NAME, phone=PHONE, doctor_name=DOCTOR_NAME, appointment_dt=appt_dt,
        amount=AMOUNT, drive_link=drive_link, payment_type=PAYMENT_TYPE,
        payment_method_override="PIX", receipt_filename=final_filename,
    )
    print("planilha Pagamentos: linha anexada")

    await log_event("payment_receipt_registered", PHONE, {
        "patient_name": PATIENT_NAME, "amount": AMOUNT, "payment_type": PAYMENT_TYPE,
        "drive_link": drive_link, "file_md5": md5, "payer": PAYER, "paid_at": PAID_AT,
        "appointment_id": new_event_id, "manual": True, "source": "manual_oneoff_script",
        "reason": "comprovante dropado na ingestao; consulta reativada e taxa lancada a mao",
    })
    print("evento payment_receipt_registered gravado")

    try:
        await client.from_("messages").insert({
            "phone": msg_phone, "role": "user",
            "content": (f"[imagem]: COMPROVANTE DE PAGAMENTO: {PAYMENT_TYPE} R$ {AMOUNT} "
                        f"— registrado pela atendente [drive_link:{drive_link}]"),
        }).execute()
        print("espelho em messages: ok")
    except Exception as e:
        print("espelho em messages FALHOU (não crítico):", e)

    await send_clinic_notification_email(
        subject=f"Comprovante recebido — {PATIENT_NAME}",
        body=(f"💰 Comprovante recebido!\nPaciente: {PATIENT_NAME}\nPagadora: {PAYER}\nValor: R$ {AMOUNT}\n"
              f"Tipo: {PAYMENT_TYPE}\nConsulta: {DOCTOR_NAME} — {appt_dt} ({MODALITY})\nLink: {drive_link}\n\n"
              f"(Lançado à mão — comprovante enviado em {PAID_AT} havia sido dropado na ingestão; "
              f"a consulta cancelada pelo cron foi recolocada.)"),
    )
    print("e-mail clínica: enviado")

    # ── 3. Aviso ao contato ─────────────────────────────────────────────────────
    sent_text = None
    if DO_SEND:
        if window_open:
            await send_text(THREAD, patient_text)
            sent_text, via = patient_text, "texto"
        else:
            await send_template_message(
                CW_CONV, "pagamento_taxa_recebido", "pt_BR", "UTILITY",
                {"1": CONTACT_FIRST, "2": f"R$ {AMOUNT}", "3": f"a consulta de {PATIENT_NAME.split()[0]}"}, template_text,
            )
            sent_text, via = template_text, "template"
        print(f"WhatsApp ao contato: ENVIADO via {via}")
        try:
            await client.from_("messages").insert({"phone": msg_phone, "role": "assistant", "content": sent_text}).execute()
        except Exception as e:
            print("espelho da resposta em messages FALHOU (não crítico):", e)
    else:
        print("WhatsApp ao contato NÃO enviado (DO_SEND!=1). Texto previsto:")
        print("   >>>", patient_text if window_open else template_text)

    # ── 4. Nota privada no Chatwoot ───────────────────────────────────────────
    note = (
        f"🤖 Correção manual (Ayexa, {datetime.now(TZ).strftime('%d/%m %H:%M')}): o comprovante Pix de R$ {AMOUNT} "
        f"enviado por {CONTACT_FIRST} em {PAID_AT} não foi lido pela Eva (falha na ingestão da imagem) e a consulta de "
        f"{PATIENT_NAME.split()[0]} foi cancelada automaticamente pelo cron por falta de taxa. Feito: consulta recolocada com a "
        f"{DOCTOR_NAME} em {appt_dt} ({MODALITY}), evento recriado no Google Calendar, taxa registrada "
        f"(Drive + planilha Pagamentos), e-mail à clínica enviado"
        + (f", {CONTACT_FIRST} avisada(o) por WhatsApp ({via})." if sent_text else ", {CONTACT_FIRST} AINDA NÃO avisada(o).")
        + " Eva segue pausada (eva-inativa) por decisão da atendente."
    )
    await add_private_note(CW_CONV, note)
    print("nota privada no Chatwoot: ok")
    print("\nOK.")


asyncio.run(main())
