"""Agendamento feito pela atendente no painel (endpoint /admin/panel/appointments).

Reaproveita as regras da Eva: grade (grid_violation), textos (booking_texts),
destinatários pela regra da idade (consultation_reminder_contacts). A mensagem
enviada entra em `messages` e no checkpoint da conversa, para a Eva saber do que
se trata quando o paciente responder.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.booking_texts import DOCTOR_LABELS, confirmation_text, format_appt_line
from app.database import DOCTOR_IDS, get_supabase, log_event, save_message
from app.google_calendar import grid_violation
from app.patients import (
    _is_held,
    _linked_contacts_with_marker,
    consultation_reminder_contacts,
    get_contact_by_phone,
    get_patient_by_id,
)
from app.phone import _phone_variants
from app.utils import display_name
from app.whatsapp import send_text


class BookingError(Exception):
    """Falha ao gravar; nada ficou criado (eventos do Calendar já apagados)."""

_logger = logging.getLogger(__name__)
TZ = ZoneInfo("America/Recife")

# Mesmos textos em dashboard/templates/atendente.html (SPLIT_PART1/SPLIT_PART2).
SPLIT_PART1 = "1ª consulta · parte 1 de 2"
SPLIT_PART2 = "1ª consulta · parte 2 de 2"

_GRID_REASONS = {
    "dia_bloqueado": "dia bloqueado na agenda",
    "fora_da_excecao": "fora do horário de atendimento",
    "dia_sem_atendimento": "o médico não atende neste dia da semana",
    "fora_da_grade": "fora do horário de atendimento",
    "estoura_expediente": "passa do fim do expediente",
}

# Tasks de _notify_clinic_async precisam de uma referência forte enquanto rodam —
# senão o garbage collector pode derrubar a task no meio (asyncio só guarda uma
# referência fraca em create_task). O callback tira a task do set quando termina.
_background_tasks: set[asyncio.Task] = set()


_LOWERCASE_CONNECTORS = {"de", "da", "do", "das", "dos", "e"}


def _nice(name: str) -> str:
    """Nome em CAIXA ALTA vira Title Case antes de aparecer numa mensagem/aviso
    ('MARIA DE FÁTIMA' -> 'Maria de Fátima'); conectores portugueses (de/da/do/
    das/dos/e) ficam em minúsculo, exceto na primeira palavra. Nomes já normais
    não são tocados."""
    if not (name and name.isupper()):
        return name
    words = name.title().split(" ")
    return " ".join(
        w.lower() if i > 0 and w.lower() in _LOWERCASE_CONNECTORS else w
        for i, w in enumerate(words)
    )


def _ensure_tz(dt: datetime) -> datetime:
    """Garante que `dt` está em America/Recife. Aceita naive (assume Recife) ou
    aware em qualquer fuso (converte)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _to_local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


async def _calendar_busy(doctor: str, start: datetime, end: datetime) -> list[dict]:
    """Eventos da Eva no Calendar do médico entre start e end. Erro = lista vazia
    (a checagem no banco continua valendo)."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import _credentials, _get_busy
    from googleapiclient.discovery import build
    try:
        calendar_id = await _get_doctor_calendar_id(doctor)
        service = build("calendar", "v3", credentials=_credentials())
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _get_busy, service, calendar_id, start, end)
    except Exception:
        _logger.exception("panel check_slot: falha ao ler o Calendar doctor=%s", doctor)
        return []


async def check_slot(doctor: str, start: datetime, minutes: int, patient_id: str,
                     exclude_appointment_id: str | None = None) -> list[str]:
    """Motivos pelos quais o horário é encaixe. Lista vazia = horário livre na grade.

    `patient_id` é mantido na assinatura só por compatibilidade com o chamador
    (o endpoint do painel sempre passa o paciente sendo agendado), mas NÃO exclui
    mais os conflitos do próprio paciente: duas consultas do mesmo paciente no
    mesmo horário também são um conflito real (evita duplicidade por engano), e
    os intervalos [start, end) vindos do banco já não se sobrepõem por causa do
    `lt`/`gt` estritos — então a 2ª parte da 1ª consulta infantil, que só encosta
    (não sobrepõe) na 1ª, nunca dependeu dessa exclusão.

    `exclude_appointment_id`: na edição, a própria consulta não conta como
    conflito (nem no banco nem no Calendar)."""
    del patient_id  # não usado para exclusão (ver docstring)
    start = _ensure_tz(start)
    reasons: list[str] = []
    code = grid_violation(doctor, start, minutes)
    if code:
        reasons.append(_GRID_REASONS[code])

    end = start + timedelta(minutes=minutes)
    client = await get_supabase()
    query = (
        client.from_("appointments")
        .select("patient_id, start_time, patients(name)")
        .eq("doctor_id", DOCTOR_IDS[doctor])
        .eq("status", "scheduled")
        .lt("start_time", end.isoformat())
        .gt("end_time", start.isoformat())
    )
    if exclude_appointment_id:
        query = query.neq("appointment_id", exclude_appointment_id)
    res = await query.execute()
    clash_starts = set()
    for row in res.data or []:
        other = _nice(display_name((row.get("patients") or {}).get("name") or "")) or "outro paciente"
        hhmm = _to_local(row["start_time"]).strftime("%H:%M")
        clash_starts.add(hhmm)
        reasons.append(f"bate com a consulta de {other} às {hhmm}")

    # Calendar pega também o que foi marcado à mão pela clínica (evento "Consulta ...").
    for ev in await _calendar_busy(doctor, start, end):
        if exclude_appointment_id and ev.get("id") == exclude_appointment_id:
            continue
        hhmm = _to_local(ev["start"]).strftime("%H:%M")
        if hhmm not in clash_starts:
            reasons.append(f"agenda do médico ocupada às {hhmm}")
            clash_starts.add(hhmm)
    return reasons


def _notify_clinic_async(subject: str, body: str, phone: str) -> None:
    from app.graph.tools import _notify_clinic
    task = asyncio.create_task(_notify_clinic(body, phone=phone, subject=subject))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _fee_fields(req: dict, now_iso: str) -> tuple[dict, str]:
    """Campos de taxa da linha e o tipo de mensagem (booking_texts.confirmation_text).

    Cortesia por ficha (`patients.custom_price == 0`) vale para qualquer consulta
    do paciente, então entra na conta mesmo quando a atendente escolheu outra
    cobrança no painel — igual à regra de `send_payment_reminders`/`patient_attributes`
    (Task 4). Ao completar a 2ª parte de uma 1ª consulta dividida, os campos da 1ª
    parte se somam (OR) com a cobrança escolhida agora para a 2ª — nunca "perdem"
    uma cortesia/isenção já concedida, do mesmo jeito que `confirm_appointment` faz
    ao herdar `booking_fee_waived`/`booking_fee_paid_at` do `_split_sibling`."""
    patient = req["patient"]
    custom_price_zero = patient.get("custom_price") == 0
    part1 = req.get("split_of")
    if part1:
        is_courtesy = bool(part1.get("is_courtesy")) or req["billing"] == "cortesia" or custom_price_zero
        waived = is_courtesy or bool(part1.get("booking_fee_waived")) or req["billing"] in ("taxa_isenta", "cortesia")
        paid_at = part1.get("booking_fee_paid_at") or (now_iso if waived else None)
        fields = {"booking_fee_waived": waived, "booking_fee_paid_at": paid_at, "is_courtesy": is_courtesy}
        if is_courtesy:
            kind = "cortesia"
        elif waived:
            kind = "taxa_isenta"
        elif paid_at:
            kind = "taxa_paga"
        else:
            kind = "normal"
        return fields, kind

    billing = req["billing"]
    is_courtesy = billing == "cortesia" or custom_price_zero
    if is_courtesy:
        # booking_fee_waived também, para todo filtro antigo por taxa já pular a cortesia.
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": True}, "cortesia"
    if billing == "taxa_isenta":
        return {"booking_fee_waived": True, "booking_fee_paid_at": now_iso, "is_courtesy": False}, "taxa_isenta"
    return {"booking_fee_waived": False, "booking_fee_paid_at": None, "is_courtesy": False}, "normal"


def _part_notes(req: dict) -> list[str]:
    extra = (req.get("session_note") or "").strip()
    if req.get("split_of"):
        labels = [SPLIT_PART2]
    elif req.get("split"):
        labels = [SPLIT_PART1, SPLIT_PART2][: len(req["parts"])]
    else:
        labels = [""]
    return [" · ".join(x for x in (label, extra) if x) for label in labels]


def _age_on(birth_date: str | None, day) -> int | None:
    """Idade em `day` (um date), aceitando dd/mm/aaaa e ISO — mesmos formatos
    de app.patients._compute_age, mas calculada numa data arbitrária (a da
    consulta), não em date.today(): a idade que importa para a regra do menor
    é a que o paciente tinha (ou vai ter) no dia da consulta, não hoje."""
    raw = (birth_date or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            bd = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    else:
        return None
    return day.year - bd.year - ((day.month, day.day) < (bd.month, bd.day))


def _is_minor_julio(patient: dict, doctor: str, day) -> bool:
    age = _age_on(patient.get("birth_date"), day)
    return doctor == "julio" and age is not None and age < 18


async def create_appointments(req: dict) -> dict:
    """Cria o(s) evento(s) e a(s) linha(s). Tudo ou nada."""
    from app.graph.tools import _get_doctor_calendar_id
    from app.google_calendar import cancel_event, create_event

    doctor = req["doctor"]
    patient = req["patient"]
    name = patient.get("name") or ""
    display = f"{name} ({patient['social_name']})" if patient.get("social_name") else name
    try:
        calendar_id = await _get_doctor_calendar_id(doctor)
    except Exception as exc:
        raise BookingError("calendário do médico não encontrado") from exc
    if not calendar_id:
        raise BookingError("calendário do médico não encontrado")

    parts = [{"start": _ensure_tz(p["start"]), "minutes": p["minutes"]} for p in req["parts"]]
    notes = _part_notes(req)
    if len(notes) != len(parts):
        raise BookingError("parts e session_note incompatíveis (split malformado)")

    now_iso = datetime.now(TZ).isoformat()
    fee, kind = _fee_fields(req, now_iso)
    # Split (1ª consulta infantil em duas sessões) é sempre primeira_consulta,
    # mesmo que a atendente esqueça de marcar a caixinha.
    if req["first_consultation"] or req.get("split") or req.get("split_of"):
        ctype = "primeira_consulta"
    elif _is_minor_julio(patient, doctor, parts[0]["start"].date()):
        ctype = "acompanhamento"
    else:
        ctype = None

    created: list[tuple[str, datetime, int, str]] = []
    try:
        for part, note in zip(parts, notes):
            event_id = await create_event(
                calendar_id=calendar_id, start=part["start"], slot_minutes=part["minutes"],
                patient_name=display, doctor_name=DOCTOR_LABELS[doctor], session_note=note,
                modality=req["modality"], patient_email=patient.get("email") or "",
                patient_number=req["phone"],
            )
            created.append((event_id, part["start"], part["minutes"], note))

        rows = [
            {
                "patient_id": patient["id"],
                "contact_id": req["contact_id"],
                "doctor_id": DOCTOR_IDS[doctor],
                "appointment_id": event_id,
                "start_time": start.isoformat(),
                "end_time": (start + timedelta(minutes=minutes)).isoformat(),
                "status": "scheduled",
                "modality": req["modality"],
                "consultation_type": ctype,
                "session_note": note or None,
                **fee,
            }
            for event_id, start, minutes, note in created
        ]
        client = await get_supabase()
        await client.from_("appointments").insert(rows).execute()
    except Exception as exc:
        _logger.exception("panel create_appointments falhou patient=%s", patient.get("id"))
        for event_id, *_ in created:
            try:
                await cancel_event(calendar_id, event_id)
            except Exception:
                _logger.exception("rollback: falha ao apagar evento %s", event_id)
        raise BookingError("não foi possível gravar a consulta") from exc

    lines = [format_appt_line(doctor, start, note) for _, start, _, note in created]
    for event_id, start, minutes, note in created:
        await log_event("appointment_booked", req["phone"], {
            "doctor": doctor, "datetime": start.replace(tzinfo=None).isoformat(),
            "duration_minutes": minutes, "patient_name": name, "session_note": note,
            "origem": "painel", "atendente": req.get("agent") or "", "encaixe": bool(req.get("encaixe")),
            "appointment_id": event_id,
        })
    if req.get("notify_clinic", True):  # remarcação tardia manda o próprio aviso
        modality_label = "Online" if req["modality"] == "online" else "Presencial"
        _notify_clinic_async(
            f"Agendamento realizado — {display}",
            "Agendamento realizado pelo painel ✅\n"
            f"Paciente: {display}\n" + "\n".join(lines) +
            f"\nModalidade: {modality_label}\nAtendente: {req.get('agent') or '—'}"
            + ("\n⚠️ Encaixe fora da grade" if req.get("encaixe") else ""),
            req["phone"],
        )
    return {
        "kind": kind,
        "lines": lines,
        "pending_part2": bool(req.get("split")) and len(created) == 1,
        "appointments": [
            {"appointment_id": e, "start": s.isoformat(), "minutes": m, "session_note": n,
             "contact_id": req["contact_id"]}
            for e, s, m, n in created
        ],
    }


_PENDING_PART2_LINE = "O horário da 2ª parte da primeira consulta será combinado depois."
WHATSAPP_WINDOW_HOURS = 24


def _thread(phone: str) -> str:
    """Forma canônica '<dígitos-com-9>@s.whatsapp.net', igual ao que app/main.py usa
    para o thread_id do checkpoint — não basta tirar o '+', porque a mesma pessoa
    pode mandar o número com ou sem o 9º dígito (ver app/phone.py)."""
    digits = (phone or "").lstrip("+").replace("@s.whatsapp.net", "")
    variants = _phone_variants(digits)
    canonical = variants[0] if variants else digits
    return f"{canonical}@s.whatsapp.net"


def contact_first_name(contact: dict) -> str:
    """Nome curto do contato para a saudação ('CARLA MENEZES' -> 'Carla'); '' se não houver."""
    return display_name(_nice(contact.get("name") or ""))


def _text_for(contact: dict, kind: str, lines: list[str], pending_part2: bool) -> str:
    name = contact_first_name(contact) or "tudo bem"
    txt = confirmation_text(kind, "\n".join(lines), name)
    if pending_part2:
        txt += f"\n\n{_PENDING_PART2_LINE}"
    return txt


async def _recipients(patient_id: str, contact_id: str) -> tuple[list[dict], list[str]]:
    recips = await consultation_reminder_contacts(patient_id, {"contact_id": contact_id})
    linked = await _linked_contacts_with_marker(patient_id, include_inactive=True)
    held = [lc["contact"].get("name") or "sem nome" for lc in linked if _is_held(lc["contact"])]
    return recips, held


async def preview_message(patient_id: str, contact_id: str, text_for) -> dict:
    """Prévia: texto como o 1º destinatário veria, destinatários e quem está com a Eva desligada."""
    recips, held = await _recipients(patient_id, contact_id)
    first = recips[0] if recips else {}
    return {
        "text": text_for(first),
        "recipients": [{"name": c.get("name") or "", "phone_hint": (c.get("phone") or "")[-4:]} for c in recips],
        "held": held,
    }


async def message_preview(patient_id: str, contact_id: str, kind: str, lines: list[str],
                          pending_part2: bool) -> dict:
    return await preview_message(patient_id, contact_id,
                                 lambda c: _text_for(c, kind, lines, pending_part2))


_RECEIPT_BY_ATTENDANT = "registrado pela atendente"  # sufixo de dashboard/payments.mark_paid


async def _window_open(phone: str) -> bool:
    """Mesma regra de scripts/send_payment_reminders._window_open: fora de 24h da
    última mensagem do contato, o Meta descarta texto livre em silêncio.
    Erro na consulta = janela fechada (não finge que entregou)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=WHATSAPP_WINDOW_HOURS)).isoformat()
    try:
        client = await get_supabase()
        res = await (
            client.from_("messages").select("created_at, content")
            .in_("phone", _phone_variants(phone)).eq("role", "user")
            .gte("created_at", cutoff).execute()
        )
        # O comprovante anexado pela atendente entra como role='user'
        # (dashboard/payments.mark_paid), mas não foi o contato quem escreveu.
        return any(_RECEIPT_BY_ATTENDANT not in (r.get("content") or "") for r in res.data or [])
    except Exception:
        _logger.exception("panel _window_open falhou phone=%s", phone)
        return False


async def deliver_message(patient_id: str, contact_id: str, text_for, patient_name: str, doctor: str) -> dict:
    """Manda a mensagem aos destinatários com janela aberta e grava tudo que a Eva
    precisa saber depois: `messages` (auditoria/lembretes) e o checkpoint do
    LangGraph (senão o paciente responde "ok" e a Eva reconfirma um
    `pending_appointment` velho — ver nodes.py _PENDING_AFFIRMATIVE)."""
    from langchain_core.messages import AIMessage
    from app.graph import graph as graph_module

    recips, held = await _recipients(patient_id, contact_id)
    sent, not_delivered = [], []
    for c in recips:
        name = c.get("name") or "sem nome"
        phone = (c.get("phone") or "").lstrip("+")
        if not phone or not await _window_open(phone):
            not_delivered.append(name)
            continue
        text = text_for(c)
        thread = _thread(phone)
        try:
            await send_text(thread, text)
        except Exception:
            _logger.exception("panel send_text falhou phone=%s", phone)
            not_delivered.append(name)
            continue
        await save_message(thread, "assistant", text)
        try:
            cfg = {"configurable": {"thread_id": thread, "phone": thread}}
            snapshot = await graph_module.chatbot.aget_state(cfg)
            update: dict = {
                "messages": [AIMessage(content=text)],
                # pending_appointment velho não pode sobreviver: um "ok" do
                # paciente à próxima mensagem não pode reconfirmar um horário antigo.
                "pending_appointment": None,
                "phone": thread,
            }
            if not snapshot.values:
                # Thread nova (nunca conversou com a Eva): semeia o mínimo, igual
                # scripts/send_payment_reminders.save_to_checkpoint.
                update.update({
                    "stage": "patient_agent",
                    "user_name": c.get("name") or "",
                    "patient_name": patient_name,
                    "is_patient": True,
                    "preferred_doctor": doctor,
                })
            await graph_module.chatbot.aupdate_state(cfg, update, as_node="patient_agent")
        except Exception:
            _logger.exception("panel checkpoint falhou phone=%s", phone)
        sent.append(name)
    return {"sent": sent, "not_delivered": not_delivered, "held": held}


async def send_booking_message(patient_id: str, contact_id: str, kind: str, lines: list[str],
                               pending_part2: bool, patient_name: str, doctor: str) -> dict:
    return await deliver_message(patient_id, contact_id,
                                 lambda c: _text_for(c, kind, lines, pending_part2),
                                 patient_name, doctor)


class PanelInputError(ValueError):
    """Corpo do endpoint inválido — vira 400 em app/main.py."""


def _now() -> datetime:
    """Agora em America/Recife — função à parte para os testes poderem
    substituir sem mexer no relógio real (ex.: checar horário no passado)."""
    return datetime.now(TZ)


def _parse_start(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=TZ)
    except (TypeError, ValueError):
        raise PanelInputError(f"data/hora inválida: {raw}")


async def build_request(body: dict) -> dict:
    """Valida o corpo do painel e devolve o dict que create_appointments espera."""
    doctor = body.get("doctor")
    if doctor not in DOCTOR_IDS:
        raise PanelInputError("médico inválido")
    modality = body.get("modality")
    if modality not in ("online", "presencial"):
        raise PanelInputError("modalidade inválida")
    billing = body.get("billing", "normal")
    if billing not in ("normal", "taxa_isenta", "cortesia"):
        raise PanelInputError("cobrança inválida")

    phone = _thread(body.get("phone") or "")
    contact = await get_contact_by_phone(phone)
    if not contact:
        raise PanelInputError("contato não encontrado")
    patient = await get_patient_by_id(body.get("patient_id") or "")
    if not patient:
        raise PanelInputError("paciente não encontrado")

    restriction = patient.get("modality_restriction")
    if restriction in ("online", "presencial") and modality != restriction:
        raise PanelInputError(f"este paciente só pode ser atendido {restriction}")

    raw_parts = body.get("parts") or []
    if not 1 <= len(raw_parts) <= 2:
        raise PanelInputError("informe um ou dois horários")
    parts = []
    now = _now()
    for p in raw_parts:
        if not isinstance(p, dict):
            raise PanelInputError("horário inválido")
        try:
            minutes = int(p.get("minutes") or 0)
        except (TypeError, ValueError):
            raise PanelInputError("duração inválida")
        if minutes not in (40, 60, 120) or (minutes == 40 and doctor != "bruna"):
            raise PanelInputError("duração inválida")
        start = _parse_start(p.get("start"))
        if start < now:
            raise PanelInputError("esse horário já passou")
        parts.append({"start": start, "minutes": minutes})

    if len(parts) == 2:
        end1 = parts[0]["start"] + timedelta(minutes=parts[0]["minutes"])
        if parts[1]["start"] < end1:
            raise PanelInputError("a 2ª parte precisa ser depois da 1ª")

    split = bool(body.get("split"))
    split_of_id = body.get("split_of")
    split_of = None
    minor_julio = _is_minor_julio(patient, doctor, parts[0]["start"].date())
    if split or split_of_id:
        if not (minor_julio and body.get("first_consultation")):
            raise PanelInputError("divisão só vale para a 1ª consulta de menor com o Dr. Júlio")
        if any(p["minutes"] != 60 for p in parts):
            raise PanelInputError("cada parte da 1ª consulta dividida tem 1h")
    if len(parts) == 2 and not split:
        raise PanelInputError("dois horários só na 1ª consulta dividida")
    if split_of_id:
        if len(parts) != 1:
            raise PanelInputError("completar a 2ª parte usa um horário só")
        client = await get_supabase()
        res = await (
            client.from_("appointments")
            .select("appointment_id, patient_id, booking_fee_paid_at, booking_fee_waived, is_courtesy, session_note, status")
            .eq("appointment_id", split_of_id).limit(1).execute()
        )
        split_of = (res.data or [None])[0]
        if not split_of or split_of["patient_id"] != patient["id"] \
                or split_of.get("status") not in ("scheduled", "completed") \
                or not (split_of.get("session_note") or "").startswith(SPLIT_PART1):
            raise PanelInputError("parte 1 pendente não encontrada")
        # A 2ª parte pode já ter sido marcada noutra sessão (ou reagendada) —
        # sem isto, dois cliques em "Marcar 2ª parte" criariam duas partes 2.
        part2_check = await (
            client.from_("appointments")
            .select("session_note")
            .eq("patient_id", patient["id"])
            .in_("status", ["scheduled", "pending_reschedule", "completed"])
            .execute()
        )
        if any((r.get("session_note") or "").startswith(SPLIT_PART2) for r in (part2_check.data or [])):
            raise PanelInputError("a 2ª parte já foi marcada")

    effective_first = bool(body.get("first_consultation")) and minor_julio

    return {
        "phone": phone, "contact_id": contact["id"], "patient": patient, "doctor": doctor,
        "modality": modality, "parts": parts, "split": split and not split_of_id, "split_of": split_of,
        "session_note": (body.get("session_note") or "").strip()[:80],
        "first_consultation": effective_first, "billing": billing,
        "encaixe": False, "agent": (body.get("agent") or "").strip()[:80],
    }


async def handle(body: dict) -> tuple[int, dict]:
    """Fluxo do endpoint POST /admin/panel/appointments. Devolve (status_http, corpo)."""
    req = await build_request(body)
    reasons: list[str] = []
    if not body.get("encaixe_confirmed"):
        for part in req["parts"]:
            reasons += await check_slot(req["doctor"], part["start"], part["minutes"], req["patient"]["id"])

    if body.get("dry_run"):
        _, kind = _fee_fields(req, datetime.now(TZ).isoformat())
        lines = [format_appt_line(req["doctor"], p["start"], n) for p, n in zip(req["parts"], _part_notes(req))]
        pending = req["split"] and len(req["parts"]) == 1
        preview = await message_preview(req["patient"]["id"], req["contact_id"], kind, lines, pending)
        return 200, {"encaixe_reasons": reasons, "message": preview}

    if reasons:
        return 409, {"detail": {"needs_encaixe": True, "reasons": reasons}}

    req["encaixe"] = bool(body.get("encaixe_confirmed"))
    created = await create_appointments(req)
    name = req["patient"].get("name") or ""
    display_patient_name = f"{name} ({req['patient']['social_name']})" if req["patient"].get("social_name") else name
    msg = await send_booking_message(
        req["patient"]["id"], req["contact_id"], created["kind"], created["lines"], created["pending_part2"],
        display_patient_name, req["doctor"],
    )
    return 200, {"appointments": created["appointments"], "message": msg}


_PAYMENT_TEMPLATES = {"pagamento_taxa_recebido", "pagamento_consulta_recebido"}


async def send_payment_confirmation(body: dict) -> tuple[int, dict]:
    """Confirmação de pagamento dada pela atendente no painel.

    O painel monta o texto e o template (dashboard/attendant_routes.payment_confirmation)
    e a Eva entrega, porque é ela que tem as credenciais do Chatwoot que funcionam.
    Antes o próprio dashboard postava no Chatwoot e a mensagem nunca saía.
    Dentro da janela de 24h vai o texto livre; fora dela, o template aprovado.
    """
    phone = (body.get("phone") or "").strip()
    text = (body.get("text") or "").strip()
    template = body.get("template") or ""
    params = body.get("params") or {}
    if not phone or not text or template not in _PAYMENT_TEMPLATES or not isinstance(params, dict):
        raise PanelInputError("phone, text, template e params são obrigatórios")

    from app.chatwoot import find_or_create_conversation, send_template_message

    thread = _thread(phone)
    if await _window_open(phone):
        await send_text(thread, text)
        via = "texto"
    else:
        conversation_id = await find_or_create_conversation(thread)
        await send_template_message(
            conversation_id, template, "pt_BR", "UTILITY",
            {str(k): str(v) for k, v in params.items()}, text,
        )
        via = "template"
    try:
        await save_message(thread, "assistant", text)
    except Exception:
        _logger.exception("panel payment confirmation save_message falhou phone=%s", phone)
    try:
        from langchain_core.messages import AIMessage
        from app.graph import graph as graph_module
        cfg = {"configurable": {"thread_id": thread, "phone": thread}}
        snapshot = await graph_module.chatbot.aget_state(cfg)
        if snapshot.values:
            await graph_module.chatbot.aupdate_state(
                cfg, {"messages": [AIMessage(content=text)]}, as_node="patient_agent",
            )
    except Exception:
        _logger.exception("panel payment confirmation checkpoint falhou phone=%s", phone)
    return 200, {"sent": True, "via": via}
