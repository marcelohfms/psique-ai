"""One-off — força o lembrete de taxa de reserva SÓ para a consulta de 06/08 da
Maria Beatriz Cavalcante Zamorano (contato financeiro Marcia, 5581996566872).

Reaproveita as funções do cron send_payment_reminders.py para o texto e o
checkpoint ficarem idênticos ao fluxo automático. NÃO processa outros pacientes
nem executa a etapa de auto-cancelamento.

Marca payment_reminder_sent_at = agora (igual ao cron) — isso inicia o relógio de
2h: se a taxa não for paga/registrada, o cron real auto-cancela a consulta 2h depois.
"""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo("America/Recife")
APPT_ID = "lj1en6o3qbkb26fir3n0ce9l8o"  # ID do evento gcal (vinculado no fix anterior)


async def main():
    from supabase import acreate_client
    from scripts.send_payment_reminders import (
        DOCTOR_LABELS,
        DOCTOR_KEYS,
        payment_reminder_message,
        send_whatsapp,
        get_financial_contacts,
        save_to_checkpoint,
    )
    from app.utils import display_name as _dn

    client = await acreate_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    now = datetime.now(TZ)

    # --- Carrega e valida a consulta ---
    r = await (
        client.from_("appointments")
        .select("appointment_id, start_time, doctor_id, status, booking_fee_paid_at, booking_fee_waived, payment_reminder_sent_at, patient_id, patients(name)")
        .eq("appointment_id", APPT_ID)
        .single()
        .execute()
    )
    appt = r.data
    print("Consulta:", appt)
    assert appt["status"] == "scheduled", f"status={appt['status']} — abortando"
    assert appt["booking_fee_paid_at"] is None, "booking_fee_paid_at já preenchido — abortando"
    assert appt["booking_fee_waived"] is False, "taxa isenta — abortando"
    if appt["payment_reminder_sent_at"] is not None:
        print(f"⚠️  payment_reminder_sent_at já era {appt['payment_reminder_sent_at']} — reenviando mesmo assim.")

    patient_id = appt["patient_id"]
    patient_name = (appt.get("patients") or {}).get("name", "paciente")
    start_dt = datetime.fromisoformat(appt["start_time"]).astimezone(TZ)
    date_str = start_dt.strftime("%d/%m/%Y às %H:%M")
    doctor_label = DOCTOR_LABELS.get(appt.get("doctor_id", ""), "médico(a)")
    doctor_key = DOCTOR_KEYS.get(appt.get("doctor_id", ""), "")

    financial_contacts = await get_financial_contacts(client, patient_id)
    print("Contatos financeiros:", financial_contacts)
    assert financial_contacts, "nenhum contato financeiro — abortando"

    # --- Checkpointer p/ salvar a mensagem no histórico (igual ao cron) ---
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph

        pg_conn = await AsyncConnection.connect(conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row)
        graph = build_graph(checkpointer=AsyncPostgresSaver(pg_conn))

    try:
        any_sent = False
        for contact in financial_contacts:
            phone = contact["phone"]
            contact_first = _dn(contact["name"] or patient_name)
            patient_first = _dn(patient_name) if contact["name"] and contact["name"] != patient_name else None
            message = payment_reminder_message(contact_first, doctor_label, date_str, patient_first)
            print("\n--- Mensagem ---\n" + message + "\n----------------")
            try:
                await send_whatsapp(phone, message)
                any_sent = True
                print(f"✅ Enviado para {phone}")
            except Exception as e:
                print(f"❌ Falha ao enviar para {phone}: {e}")
            if graph:
                try:
                    await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
                    print("  checkpoint atualizado")
                except Exception as e:
                    print(f"  save_to_checkpoint falhou (não-fatal): {e}")

        if any_sent:
            await client.from_("appointments").update({
                "payment_reminder_sent_at": now.isoformat(),
            }).eq("appointment_id", APPT_ID).execute()
            print(f"\npayment_reminder_sent_at = {now.isoformat()} (relógio de auto-cancelamento de 2h iniciado)")
        else:
            print("\nNenhum envio bem-sucedido — payment_reminder_sent_at NÃO atualizado.")
    finally:
        if pg_conn:
            await pg_conn.close()


asyncio.run(main())
