"""
Send WhatsApp return reminders (retorno periódico) via Meta Cloud API templates.
Runs once a day via GitHub Actions.

Reminders are driven by `return_reminders` (populated by the /retornos
dashboard, where the doctor sets a return_interval per patient after seeing
them). next_return_date is compared to today by CALENDAR MONTH, not exact
days:
  - month before next_return_date's month -> retorno_um_mes_antes
  - same month as next_return_date        -> retorno_no_mes
  - after next_return_date's month        -> retorno_atrasado (envio único)

`15_dias` rows never fire (all 3 flags pre-marked at classification time —
see dashboard/return_reminders.py::save_classification). `1_mes` rows never
fire retorno_um_mes_antes for the same reason (that flag is also pre-marked).

Sends are throttled — batches of 10, 60s pause between batches — to reduce
risk of the WhatsApp number being flagged for spam. Only the flag for a
successfully-sent reminder is marked, so anything left over from today's run
is retried automatically on tomorrow's run.

Requires in Supabase: tabela `return_reminders` (ver
supabase/migrations/20260714_create_return_reminders.sql).
"""
import asyncio
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.patients import get_contacts_for_patient

TZ = ZoneInfo("America/Recife")
BATCH_SIZE = 10
BATCH_PAUSE_SECONDS = 60

DOCTOR_LABELS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}
DOCTOR_KEYS = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}


def _month_key(d: date) -> int:
    return d.year * 12 + d.month


def pending_template(today: date, row: dict) -> tuple[str, str] | None:
    """Retorna (template_name, coluna_de_flag) a disparar hoje para esta linha, ou None."""
    if row.get("return_interval") == "15_dias":
        return None

    next_return_date = date.fromisoformat(row["next_return_date"])
    current_key = _month_key(today)
    target_key = _month_key(next_return_date)

    if row.get("month_before_sent_at") is None and current_key == target_key - 1:
        return ("retorno_um_mes_antes", "month_before_sent_at")
    if row.get("month_of_sent_at") is None and current_key == target_key:
        return ("retorno_no_mes", "month_of_sent_at")
    if row.get("overdue_sent_at") is None and current_key > target_key:
        return ("retorno_atrasado", "overdue_sent_at")
    return None


def _plain_message(template_name: str, first_name: str, doctor_label: str) -> str:
    if template_name == "retorno_um_mes_antes":
        return (
            f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Passando para avisar que "
            f"seu retorno com {doctor_label} está previsto para o mês que vem.\n\n"
            f"Manter a regularidade das consultas é fundamental para o acompanhamento do seu "
            f"tratamento, especialmente considerando que a renovação de receitas de medicamentos "
            f"controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código "
            f"de Ética Médica. Assim você evita ficar sem acesso à medicação quando chegar a hora."
            f"\n\nSe quiser já deixar reservado um horário, é só nos avisar por aqui!"
        )
    if template_name == "retorno_no_mes":
        return (
            f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Verificamos que você está "
            f"no período indicado para a sua próxima consulta com {doctor_label}, gostaria de "
            f"agendar?\n\nManter a regularidade das consultas é fundamental para o acompanhamento "
            f"do seu tratamento. Além disso, a renovação de receitas de medicamentos controlados "
            f"depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), então "
            f"agendar em dia é importante para que você não fique sem acesso à medicação."
            f"\n\nEstamos à disposição para agendar o horário que melhor se encaixa para você!"
        )
    return (
        f"Olá! Tudo bem? 😊\n\nAqui é a Eva, secretária da Psiquê. Notamos que o período indicado "
        f"para o seu retorno com {doctor_label} já passou. Como o acompanhamento regular é "
        f"importante para a continuidade do seu tratamento, ficamos à disposição para remarcar o "
        f"quanto antes.\n\nVale lembrar também que a renovação de receitas de medicamentos "
        f"controlados depende de reavaliação médica periódica (Art. 37 do Código de Ética Médica), "
        f"então quanto antes retomarmos as consultas, menor o risco de você ficar sem acesso à "
        f"medicação.\n\nSe puder nos responder com sua disponibilidade, já organizamos um horário "
        f"para você."
    )


async def send_return_reminder_template(phone: str, template_name: str, first_name: str, doctor_label: str) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    plain = _plain_message(template_name, first_name, doctor_label)
    await send_template_message(
        conv_id,
        template_name=template_name,
        language="pt_BR",
        category="UTILITY",
        body_params={"1": first_name, "2": doctor_label},
        content=plain,
    )


async def save_to_checkpoint(graph, phone: str, message: str, patient_name: str, doctor_key: str) -> None:
    """Injeta o lembrete no checkpoint do LangGraph.

    Só inicializa stage/is_patient/preferred_doctor para conversa NOVA — ao
    contrário de send_appointment_reminders.py, que sempre reescreve esses
    campos. Escolha deliberada: este lembrete é um nudge informativo mensal,
    não um evento crítico do fluxo de agendamento, e sobrescrever o estado de
    uma conversa já em andamento (ex: paciente no meio de um cadastro) arrisca
    corromper esse fluxo à toa — o mesmo tipo de bug já visto quando uma nota
    da atendente forçava stage e quebrava collect_info.
    """
    from langchain_core.messages import AIMessage
    thread_phone = f"{phone}@s.whatsapp.net"
    config = {"configurable": {"thread_id": thread_phone, "phone": thread_phone}}
    snapshot = await graph.aget_state(config)
    update: dict = {"messages": [AIMessage(content=message)]}
    if not snapshot.values:
        update.update({
            "phone": thread_phone,
            "stage": "patient_agent",
            "user_name": patient_name,
            "patient_name": patient_name,
            "is_patient": True,
            "preferred_doctor": doctor_key,
        })
    await graph.aupdate_state(config, update, as_node="patient_agent")


async def _has_future_appointment(client, patient_id: str, doctor_id: str, now_iso: str) -> bool:
    result = await (
        client.from_("appointments")
        .select("id")
        .eq("patient_id", patient_id)
        .eq("doctor_id", doctor_id)
        .eq("status", "scheduled")
        .gt("end_time", now_iso)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def _send_for_row(client, row: dict, template_name: str, sent_col: str, graph) -> None:
    """Envia `template_name` a todos os contatos 'consulta' do paciente.

    Marca `sent_col` UMA vez, só se ao menos um envio teve sucesso (retries
    do cron permanecem seguros). Sem contato de consulta: não envia, não
    marca — a linha continua candidata nos próximos dias (visível via print
    de [SKIP], sem retry automático de outra natureza).

    Inclui contatos pausados (`include_inactive=True`), mesmo padrão de
    send_appointment_reminders.py: acesso a medicação controlada depende de
    retorno em dia (Art. 37 CEM, citado no próprio corpo do lembrete), então
    pausa do bot não deve silenciar esse aviso — mesma lógica de
    app/patients.py::get_contacts_for_patient para lembretes transacionais.
    """
    patient_id = row.get("patient_id")
    patient = row.get("patients") or {}
    patient_name = patient.get("name") or "paciente"
    from app.utils import display_name as _dn
    doctor_label = DOCTOR_LABELS.get(row.get("doctor_id", ""), "médico(a)")
    doctor_key = DOCTOR_KEYS.get(row.get("doctor_id", ""), "")

    contacts = await get_contacts_for_patient(patient_id, "consulta", include_inactive=True) if patient_id else []
    if not contacts:
        print(f"  [SKIP] return_reminder {row.get('id')} sem contato de consulta (patient_id={patient_id})")
        return

    sent_any = False
    for contact in contacts:
        phone = contact.get("phone")
        if not phone:
            continue
        first_name = _dn(contact.get("name") or patient_name)
        try:
            await send_return_reminder_template(phone, template_name, first_name, doctor_label)
            message = _plain_message(template_name, first_name, doctor_label)
            if graph:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            print(f"  [{template_name}] Sent to {phone} — {patient_name}")
            sent_any = True
        except Exception as e:
            print(f"  Failed to send to {phone}: {e}")

    if sent_any:
        await client.from_("return_reminders").update({
            sent_col: datetime.now(TZ).isoformat(),
        }).eq("id", row["id"]).execute()


async def main():
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    today = datetime.now(TZ).date()
    now_iso = datetime.now(TZ).isoformat()

    result = await (
        client.from_("return_reminders")
        .select(
            "id, patient_id, doctor_id, return_interval, next_return_date, "
            "month_before_sent_at, month_of_sent_at, overdue_sent_at, patients(name)"
        )
        .neq("return_interval", "15_dias")
        .execute()
    )
    rows = result.data or []

    candidates = []
    for row in rows:
        pending = pending_template(today, row)
        if not pending:
            continue
        patient_id = row.get("patient_id")
        doctor_id = row.get("doctor_id")
        if patient_id and doctor_id and await _has_future_appointment(client, patient_id, doctor_id, now_iso):
            print(f"  [SKIP] return_reminder {row.get('id')} — já tem consulta futura com o mesmo médico.")
            continue
        template_name, sent_col = pending
        candidates.append((row, template_name, sent_col))

    print(f"Return reminders to send today: {len(candidates)}")

    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    graph = None
    pg_conn = None
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        pg_conn = await AsyncConnection.connect(
            conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
        )
        checkpointer = AsyncPostgresSaver(pg_conn)
        graph = build_graph(checkpointer=checkpointer)
    else:
        print("SUPABASE_CONNECTION_STRING not set — reminders won't be saved to LangGraph checkpoint.")

    try:
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            for row, template_name, sent_col in batch:
                await _send_for_row(client, row, template_name, sent_col, graph)
            if i + BATCH_SIZE < len(candidates):
                await asyncio.sleep(BATCH_PAUSE_SECONDS)
    finally:
        if pg_conn:
            await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
