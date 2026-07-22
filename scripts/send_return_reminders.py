"""
Send WhatsApp return reminders (retorno periódico) via Meta Cloud API templates.
Runs once a day via GitHub Actions.

Reminders are driven by `return_reminders` (populated by the /retornos
dashboard, where the doctor sets a return_interval per patient after seeing
them). next_return_date is compared to today by CALENDAR MONTH, not exact
days:
  - month before next_return_date's month -> retorno_mes_anterior
  - same month as next_return_date        -> retorno_no_mes
  - after next_return_date's month        -> retorno_atrasado (envio único)

`15_dias` rows never fire (all 3 flags pre-marked at classification time —
see dashboard/return_reminders.py::save_classification). `1_mes` rows never
fire retorno_mes_anterior for the same reason (that flag is also pre-marked).

Cada um dos 3 templates acima tem uma variante `_terceiro` (contato ≠
paciente, ex: mãe agendando pelo filho) — ver `_plain_message` e
`_build_body_params` abaixo.

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
        return ("retorno_mes_anterior", "month_before_sent_at")
    if row.get("month_of_sent_at") is None and current_key == target_key:
        return ("retorno_no_mes", "month_of_sent_at")
    if row.get("overdue_sent_at") is None and current_key > target_key:
        return ("retorno_atrasado", "overdue_sent_at")
    return None


# Templates "_terceiro" repetem o nome do paciente no corpo (ex: "o retorno
# de {{2}}... Assim {{2}} evita..."). A Meta não permite reusar o número de
# uma variável dentro do mesmo template aprovado, então cada repetição além
# da primeira vira uma variável NOVA (mesmo valor, número sequencial
# seguinte). Este mapa diz quantas variáveis extras cada template precisa,
# em cima do {"1": contato, "2": paciente, "3": médico} base.
_TERCEIRO_PATIENT_EXTRA_VARS = {
    "retorno_mes_anterior_terceiro": 1,  # +{{4}}
    "retorno_no_mes_terceiro": 1,        # +{{4}}
    "retorno_atrasado_terceiro": 2,      # +{{4}}, +{{5}}
}


def _build_body_params(
    template_name: str, first_name: str, doctor_label: str, patient_first_name: str | None
) -> dict[str, str]:
    """Monta os body_params por posição, incluindo as variáveis extras que
    repetem patient_first_name nos templates `_terceiro` (ver
    `_TERCEIRO_PATIENT_EXTRA_VARS`). Precisa ficar em sincronia com o texto
    de cada template em `_plain_message`.
    """
    if not patient_first_name:
        return {"1": first_name, "2": doctor_label}
    params = {"1": first_name, "2": patient_first_name, "3": doctor_label}
    for i in range(_TERCEIRO_PATIENT_EXTRA_VARS.get(template_name, 0)):
        params[str(4 + i)] = patient_first_name
    return params


def _plain_message(
    template_name: str, first_name: str, doctor_label: str, patient_first_name: str | None = None
) -> str:
    """Corpo do template. Variantes `_terceiro` (contato ≠ paciente, ex: mãe
    agendando pelo filho) usam `patient_first_name` no lugar de "seu"/"você" —
    mesma distinção de send_payment_reminders.py::payment_reminder_message,
    só que aqui precisa de template Meta separado (texto de template
    aprovado é fixo, não dá pra ter frase condicional dentro de um só).

    Nos `_terceiro`, o paciente é {{2}} e o médico é {{3}} — ordem natural
    da frase "o retorno de {{2}} com {{3}}". Repetições do nome do paciente
    usam as variáveis extras de `_TERCEIRO_PATIENT_EXTRA_VARS` ({{4}},
    {{5}}...), mas o texto sempre repete o MESMO valor de `patient_first_name`.
    """
    if template_name == "retorno_mes_anterior":
        return (
            f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
            f"Passando para avisar que seu retorno com {doctor_label} está previsto para o mês que vem.\n\n"
            f"Manter a regularidade das consultas é fundamental para o acompanhamento do seu "
            f"tratamento, especialmente considerando que a renovação de receitas de medicamentos "
            f"controlados depende de reavaliação médica periódica, conforme o Art. 37 do Código "
            f"de Ética Médica. Assim você evita ficar sem acesso à medicação quando chegar a hora."
            f"\n\nSe quiser já deixar reservado um horário, é só nos avisar por aqui! 😉"
        )
    if template_name == "retorno_mes_anterior_terceiro":
        return (
            f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
            f"Passando para avisar que o retorno de {patient_first_name} com {doctor_label} está "
            f"previsto para o mês que vem.\n\nManter a regularidade das consultas é fundamental "
            f"para o acompanhamento do tratamento, especialmente considerando que a renovação de "
            f"receitas de medicamentos controlados depende de reavaliação médica periódica, "
            f"conforme o Art. 37 do Código de Ética Médica. Assim {patient_first_name} evita "
            f"ficar sem acesso à medicação quando chegar a hora.\n\nSe quiser já deixar reservado "
            f"um horário, é só nos avisar por aqui. 😉"
        )
    if template_name == "retorno_no_mes":
        return (
            f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
            f"Verificamos que você está no período indicado para a sua próxima consulta com "
            f"{doctor_label}, gostaria de agendar?\n\nManter a regularidade das consultas é "
            f"fundamental para o acompanhamento do seu tratamento. Além disso, a renovação de "
            f"receitas de medicamentos controlados depende de reavaliação médica periódica "
            f"(Art. 37 do Código de Ética Médica), então agendar em dia é importante para que "
            f"você não fique sem acesso à medicação.\n\nEstamos à disposição para agendar o "
            f"horário que melhor se encaixa para você! 😉"
        )
    if template_name == "retorno_no_mes_terceiro":
        return (
            f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
            f"Verificamos que {patient_first_name} está no período indicado para a próxima "
            f"consulta com {doctor_label}, gostaria de agendar?\n\nManter a regularidade das "
            f"consultas é fundamental para o acompanhamento do tratamento. Além disso, a "
            f"renovação de receitas de medicamentos controlados depende de reavaliação médica "
            f"periódica (Art. 37 do Código de Ética Médica), então agendar em dia é importante "
            f"para que {patient_first_name} não fique sem acesso à medicação.\n\nEstamos à "
            f"disposição para agendar o horário que melhor se encaixa! 😉"
        )
    if template_name == "retorno_atrasado":
        return (
            f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
            f"Notamos que o período indicado para o seu retorno com {doctor_label} já passou. "
            f"Como o acompanhamento regular é importante para a continuidade do seu tratamento, "
            f"ficamos à disposição para remarcar o quanto antes.\n\nVale lembrar também que a "
            f"renovação de receitas de medicamentos controlados depende de reavaliação médica "
            f"periódica (Art. 37 do Código de Ética Médica), então quanto antes retomarmos as "
            f"consultas, menor o risco de você ficar sem acesso à medicação.\n\nSe puder nos "
            f"responder com sua disponibilidade, já organizamos um horário para você. 😉"
        )
    # retorno_atrasado_terceiro
    return (
        f"Olá, {first_name}! 😊 Aqui é a Eva, secretária virtual da Psiquê. \n\n"
        f"Notamos que o período indicado para o retorno de {patient_first_name} com {doctor_label} "
        f"já passou. Como o acompanhamento regular é importante para a continuidade do "
        f"tratamento, ficamos à disposição para remarcar o quanto antes.\n\nVale lembrar também "
        f"que a renovação de receitas de medicamentos controlados depende de reavaliação médica "
        f"periódica (Art. 37 do Código de Ética Médica), então quanto antes retomarmos as "
        f"consultas, menor o risco de {patient_first_name} ficar sem acesso à medicação.\n\nSe "
        f"puder nos responder com sua disponibilidade, já organizamos um horário para "
        f"{patient_first_name}. 😉"
    )


async def send_return_reminder_template(
    phone: str, template_name: str, first_name: str, doctor_label: str, patient_first_name: str | None = None
) -> None:
    from app.chatwoot import find_or_create_conversation, send_template_message
    phone_wpp = phone if "@s.whatsapp.net" in phone else f"{phone}@s.whatsapp.net"
    conv_id = await find_or_create_conversation(phone_wpp)
    plain = _plain_message(template_name, first_name, doctor_label, patient_first_name)
    body_params = _build_body_params(template_name, first_name, doctor_label, patient_first_name)
    await send_template_message(
        conv_id,
        template_name=template_name,
        language="pt_BR",
        category="UTILITY",
        body_params=body_params,
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
        contact_name = contact.get("name")
        first_name = _dn(contact_name or patient_name)
        # Contato diferente do paciente (ex: mãe agendando pelo filho) usa a
        # variante "_terceiro" do template, com {{2}} = nome do paciente —
        # mesma distinção de send_payment_reminders.py, só que via template
        # Meta separado (não dá pra ter frase condicional num template aprovado).
        is_terceiro = bool(contact_name and contact_name != patient_name)
        effective_template = f"{template_name}_terceiro" if is_terceiro else template_name
        patient_first_name = _dn(patient_name) if is_terceiro else None
        try:
            await send_return_reminder_template(phone, effective_template, first_name, doctor_label, patient_first_name)
            message = _plain_message(effective_template, first_name, doctor_label, patient_first_name)
            if graph:
                await save_to_checkpoint(graph, phone, message, patient_name, doctor_key)
            print(f"  [{effective_template}] Sent to {phone} — {patient_name}")
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
