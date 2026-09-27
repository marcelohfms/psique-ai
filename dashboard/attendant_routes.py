"""Rotas do painel da atendente (Fase 1).

Auth por token na query string (`?token=...`), validado contra
ATTENDANT_PANEL_TOKEN. As rotas existentes do dashboard mantêm o HTTP Basic;
estas usam o token (mais limpo dentro de um iframe do Chatwoot).
"""
import logging
import os
from datetime import date as _date
from secrets import compare_digest

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import attendant_db
import chatwoot_client
import eva_client
import payments
import return_reminders
from db_client import get_client

router = APIRouter(prefix="/api/atendente")
logger = logging.getLogger(__name__)


def verify_token(token: str = Query(default="")) -> None:
    expected = os.getenv("ATTENDANT_PANEL_TOKEN", "")
    if not expected or not compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")


_FORA_DO_ESCOPO = "objeto não pertence ao contato desta conversa"


async def _assert_contact_scope(phone: str, contact_id: str) -> None:
    scope_contact_id, _ = await attendant_db.scope_for_phone(phone)
    if scope_contact_id is None or scope_contact_id != contact_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)


async def _assert_patient_scope(phone: str, patient_id: str) -> None:
    _, patient_ids = await attendant_db.scope_for_phone(phone)
    if patient_id not in patient_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)


async def _assert_link_scope(phone: str, pc_id: str) -> None:
    contact_id, patient_ids = await attendant_db.scope_for_phone(phone)
    link = await attendant_db.get_link_by_id(pc_id)
    if link is None or (link["contact_id"] != contact_id and link["patient_id"] not in patient_ids):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)


async def _assert_appointment_scope(phone: str, appointment_id: str) -> None:
    _, patient_ids = await attendant_db.scope_for_phone(phone)
    pid = await attendant_db.get_appointment_patient_id(appointment_id)
    if pid is None or pid not in patient_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)


class UpdateBody(BaseModel):
    phone: str
    data: dict
    agent: str = Field(default="", max_length=80)


class ResetBody(BaseModel):
    phone: str


class LinkBody(BaseModel):
    phone: str
    patient_id: str
    is_self: bool
    relationship: str | None = None
    agent: str = Field(default="", max_length=80)


class NewPatientBody(BaseModel):
    phone: str
    name: str
    birth_date: str
    agent: str = Field(default="", max_length=80)


class UnlinkBody(BaseModel):
    phone: str
    patient_id: str
    agent: str = Field(default="", max_length=80)


async def _contact_id_for(phone: str) -> str:
    """Contato da conversa. O vínculo sempre usa este, nunca um id vindo do cliente."""
    contact_id, _ = await attendant_db.scope_for_phone(phone)
    if contact_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)
    return contact_id


# ── Leitura ───────────────────────────────────────────────────────────────────


@router.get("/resolve")
async def resolve(phone: str, _: None = Depends(verify_token)):
    return await attendant_db.resolve_contact_and_patients(phone)


@router.get("/paciente/{patient_id}")
async def paciente(patient_id: str, contact_id: str, _: None = Depends(verify_token)):
    patient = await attendant_db.get_patient(patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    link = await attendant_db.get_link(patient_id, contact_id)
    # Sem vínculo, o par (paciente, contato) não é desta conversa: não devolve a
    # ficha, para o token não permitir ler qualquer paciente por ID.
    if link is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORA_DO_ESCOPO)
    return_reminder = await attendant_db.get_return_reminder(patient_id)
    eva_off = await attendant_db.is_patient_eva_off(patient_id)
    return {"patient": patient, "link": link, "return_reminder": return_reminder,
            "eva_off": eva_off}


# ── Escrita ───────────────────────────────────────────────────────────────────


@router.post("/contato/{contact_id}")
async def update_contato(contact_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_contact_scope(body.phone, contact_id)
    # "name" vazio travaria o contato sem quem endereçar (cabeçalho do painel,
    # mensagens de confirmação); só recusa quando o campo está no payload.
    if "name" in body.data and not str(body.data.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="Informe o nome.")
    await attendant_db.update_contact(contact_id, body.data)
    await attendant_db.log_event("attendant_edit_contact", body.phone,
                                 {"contact_id": contact_id, "fields": list(body.data.keys()),
                                  "agent": body.agent})
    return {"ok": True}


@router.post("/paciente/{patient_id}")
async def update_paciente(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, patient_id)
    await attendant_db.update_patient(patient_id, body.data)
    await attendant_db.log_event("attendant_edit_patient", body.phone,
                                 {"patient_id": patient_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/paciente/{patient_id}/eva")
async def set_eva(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    """Liga/desliga a Eva em definitivo para o paciente inteiro (manual_hold em
    todos os contatos). body.data = {"off": true|false}."""
    await _assert_patient_scope(body.phone, patient_id)
    off = bool(body.data.get("off"))
    afetados = await attendant_db.set_patient_eva_off(patient_id, off)
    await attendant_db.log_event("attendant_set_eva_off", body.phone,
                                 {"patient_id": patient_id, "off": off, "afetados": afetados})
    return {"ok": True, "off": off, "afetados": afetados}


@router.post("/paciente/{patient_id}/retorno")
async def update_return_date(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    """Atualiza a data de retorno do paciente (tabela return_reminders).

    Só edita retorno já classificado pela médica; realinha os lembretes.
    """
    await _assert_patient_scope(body.phone, patient_id)
    raw = body.data.get("next_return_date")
    if not raw:
        raise HTTPException(status_code=400, detail="next_return_date obrigatório")
    try:
        _date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="next_return_date deve ser YYYY-MM-DD")
    updated = await attendant_db.update_return_reminder(patient_id, {"next_return_date": raw})
    await attendant_db.log_event("attendant_edit_return_date", body.phone,
                                 {"patient_id": patient_id, "next_return_date": raw, "updated": updated})
    return {"ok": True, "updated": updated}


@router.post("/vinculo/{pc_id}")
async def update_vinculo(pc_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_link_scope(body.phone, pc_id)
    data = dict(body.data)
    # O front sempre manda is_self e relationship juntos (nunca só um dos dois),
    # então basta checar a presença de qualquer um para normalizar o par inteiro.
    if "is_self" in data or "relationship" in data:
        try:
            data.update(attendant_db.normalize_marker(data.get("is_self"), data.get("relationship")))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    await attendant_db.update_link(pc_id, data)
    await attendant_db.log_event("attendant_edit_link", body.phone,
                                 {"pc_id": pc_id, "fields": list(data.keys()), "agent": body.agent})
    return {"ok": True}


# ── Vínculo: busca, vincular, ficha nova, desvincular ────────────────────────


@router.get("/pacientes/busca")
async def buscar_pacientes(
    phone: str,
    q: str = Query(..., max_length=80),
    agent: str = Query(default="", max_length=80),
    _: None = Depends(verify_token),
):
    # A busca olha a base toda (é para achar quem ainda não está ligado), então
    # devolve só nome, nascimento e 4 dígitos. Exige que o número tenha contato.
    await _contact_id_for(phone)
    results = await attendant_db.search_patients(q)
    await attendant_db.log_event("attendant_search", phone,
                                 {"q": q, "agent": agent, "results": len(results)})
    return results


@router.post("/vinculo")
async def criar_vinculo(body: LinkBody, _: None = Depends(verify_token)):
    contact_id = await _contact_id_for(body.phone)
    try:
        marker = attendant_db.normalize_marker(body.is_self, body.relationship)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if await attendant_db.get_patient(body.patient_id) is None:
        raise HTTPException(status_code=404, detail="paciente não encontrado")
    await attendant_db.link_patient(body.patient_id, contact_id, marker)
    await attendant_db.log_event("attendant_link_patient", body.phone, {
        "patient_id": body.patient_id, "contact_id": contact_id, **marker, "agent": body.agent})
    return {"ok": True}


@router.post("/paciente-novo")
async def criar_paciente(body: NewPatientBody, _: None = Depends(verify_token)):
    await _contact_id_for(body.phone)
    name = " ".join(body.name.split())
    if len(name) < 3:
        raise HTTPException(status_code=400, detail="Informe o nome completo.")
    try:
        birth = attendant_db.normalize_birth_date(body.birth_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dups = await attendant_db.find_patients_by_name_birth(name, birth)
    if dups:
        raise HTTPException(status_code=409, detail={
            "message": "Já existe ficha com este nome e nascimento.",
            "duplicates": [{"id": d["id"], "name": d.get("name"), "birth_date": d.get("birth_date")}
                           for d in dups],
        })
    try:
        patient = await attendant_db.create_patient(name, birth)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await attendant_db.log_event("attendant_create_patient", body.phone,
                                 {"patient_id": patient["id"], "agent": body.agent})
    return {"ok": True, "patient": patient}


@router.post("/desvincular")
async def desvincular(body: UnlinkBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    contact_id = await _contact_id_for(body.phone)
    blocker = await attendant_db.unlink_blocker(body.patient_id, contact_id)
    if blocker:
        raise HTTPException(status_code=409, detail={"message": blocker})
    # Lido ANTES de apagar o vínculo: depois de unlink_patient não dá mais para
    # saber, pela tabela patient_contacts, quantas consultas este número ainda
    # "está marcando" (appointments.contact_id não muda com o desvincular).
    still_booking = await attendant_db.future_bookings_by_contact(body.patient_id, contact_id)
    removed = await attendant_db.unlink_patient(body.patient_id, contact_id)
    await attendant_db.log_event("attendant_unlink_patient", body.phone, {
        "patient_id": body.patient_id, "contact_id": contact_id, "removed": removed,
        "still_booking": still_booking, "agent": body.agent})
    return {"ok": True, "removed": removed, "still_booking": still_booking}


@router.post("/reset-checkpoint")
async def reset_checkpoint(body: ResetBody, _: None = Depends(verify_token)):
    deleted = await attendant_db.reset_checkpoint(body.phone)
    await attendant_db.log_event("attendant_reset_checkpoint", body.phone, {"deleted": deleted})
    return {"ok": True, "deleted": deleted}


# ── Pagamentos ────────────────────────────────────────────────────────────────


class AtendentePagarBody(BaseModel):
    tipo: str             # "taxa" ou "consulta"
    valor: int
    forma_pagamento: str  # "PIX", "cartao_credito", "cartao_debito", "dinheiro"
    paciente: str
    medico: str
    data_hora: str
    phone: str
    conversation_id: int | None = None
    drive_link: str = ""  # link do comprovante já enviado ao Drive (ver /pagamentos/{id}/comprovante)
    receipt_filename: str = ""  # nome do arquivo no Drive, devolvido pela mesma rota


_CONFIRM_TEXT = {
    "taxa": (
        "Olá, {paciente}! 👋 Recebemos o pagamento da taxa de reserva da sua consulta "
        "com {medico}. Sua vaga está garantida! ✅"
    ),
    "consulta": (
        "Olá, {paciente}! 👋 Recebemos o pagamento da sua consulta com {medico}. Obrigado! ✅"
    ),
}


@router.get("/pagamentos")
async def pagamentos(phone: str, _: None = Depends(verify_token)):
    resolved = await attendant_db.resolve_contact_and_patients(phone)
    patient_ids = [p["id"] for p in resolved["patients"]]
    client = await get_client()
    return await payments.compute_pendencias(client, patient_ids=patient_ids)


@router.post("/pagamentos/{appointment_id}/comprovante")
async def upload_comprovante(
    paciente: str = Form(...),
    data_hora: str = Form(...),
    valor: str = Form(...),
    file: UploadFile = File(...),
    _: None = Depends(verify_token),
):
    content = await file.read()
    mimetype = file.content_type or "image/jpeg"
    try:
        drive_link, filename = await payments.upload_comprovante(paciente, data_hora, valor, content, mimetype)
    except Exception:
        logger.exception("UPLOAD_COMPROVANTE_FAILED paciente=%s", paciente)
        raise HTTPException(status_code=502, detail="Falha ao enviar comprovante ao Drive")
    return {"drive_link": drive_link, "receipt_filename": filename}


@router.post("/pagamentos/{appointment_id}/pagar")
async def pagar(appointment_id: str, body: AtendentePagarBody, _: None = Depends(verify_token)):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    await _assert_appointment_scope(body.phone, appointment_id)
    client = await get_client()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
        drive_link=body.drive_link,
        receipt_filename=body.receipt_filename,
    )

    if body.conversation_id is not None:
        try:
            text = _CONFIRM_TEXT[body.tipo].format(paciente=body.paciente, medico=body.medico)
            await chatwoot_client.send_confirmation_message(body.conversation_id, text)
        except Exception:
            logger.exception("CONFIRM_MSG_FAILED appt=%s conversation_id=%s",
                             appointment_id, body.conversation_id)

    await attendant_db.log_event("attendant_pagamento_registrado", body.phone, {
        "appointment_id": appointment_id, "tipo": body.tipo, "valor": body.valor,
    })
    return {"ok": True}


@router.post("/pagamentos/{appointment_id}/no-show")
async def pagamentos_no_show(appointment_id: str, phone: str = Query(...), _: None = Depends(verify_token)):
    """Marca a consulta como falta (no_show) a partir do painel embutido no Chatwoot.

    Espelha a rota HTTP-Basic `/api/pagamentos/{id}/no-show` do painel completo,
    mas com auth por token (o iframe do Chatwoot não tem as credenciais Basic).
    Fonte única da verdade: `return_reminders.mark_no_show`.

    `phone` (query) escopa a consulta ao contato da conversa, como as demais rotas."""
    await _assert_appointment_scope(phone, appointment_id)
    client = await get_client()
    await return_reminders.mark_no_show(client, appointment_id)
    return {"ok": True}


class AtendenteIsentarBody(BaseModel):
    paciente: str
    medico: str
    data_hora: str
    phone: str
    conversation_id: int | None = None


@router.post("/pagamentos/{appointment_id}/isentar")
async def isentar(appointment_id: str, body: AtendenteIsentarBody, _: None = Depends(verify_token)):
    """Isenta a taxa de reserva pendente — evita o cancelamento automático por falta de
    pagamento quando a atendente decide dispensar a taxa (ex: cortesia, acordo com o paciente)."""
    await _assert_appointment_scope(body.phone, appointment_id)
    client = await get_client()
    await payments.mark_fee_waived(client, appointment_id, body.paciente, body.medico, body.data_hora)

    if body.conversation_id is not None:
        try:
            text = (
                f"Olá, {body.paciente}! 👋 A taxa de reserva da sua consulta com {body.medico} "
                f"foi isentada. Não é necessário nenhum pagamento antecipado. 😊"
            )
            await chatwoot_client.send_confirmation_message(body.conversation_id, text)
        except Exception:
            logger.exception("CONFIRM_MSG_FAILED appt=%s conversation_id=%s",
                             appointment_id, body.conversation_id)

    await attendant_db.log_event("attendant_taxa_isentada", body.phone, {
        "appointment_id": appointment_id,
    })
    return {"ok": True}


# ── Consultas ─────────────────────────────────────────────────────────────────


class FirstBody(BaseModel):
    phone: str
    first: bool
    agent: str = Field(default="", max_length=80)


class NewAppointmentBody(BaseModel):
    phone: str
    patient_id: str
    doctor: str
    modality: str
    parts: list[dict] = Field(min_length=1, max_length=2)
    split: bool = False
    split_of: str | None = None
    session_note: str = Field(default="", max_length=80)
    first_consultation: bool = False
    billing: str = "normal"
    encaixe_confirmed: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


@router.get("/consultas")
async def get_consultas(phone: str, patient_id: str, _: None = Depends(verify_token)):
    await _assert_patient_scope(phone, patient_id)
    return await attendant_db.list_consultas(patient_id)


@router.post("/consulta/{appointment_id}/primeira")
async def set_primeira(appointment_id: str, body: FirstBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    await attendant_db.set_first_consultation(appointment_id, body.first)
    await attendant_db.log_event("attendant_first_consultation", body.phone,
                                 {"appointment_id": appointment_id, "first": body.first, "agent": body.agent})
    return {"ok": True}


_DRY_RUN_TIMEOUT = 20.0
_BOOKING_TIMEOUT = 60.0


@router.post("/consulta/nova")
async def nova_consulta(body: NewAppointmentBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    timeout = _DRY_RUN_TIMEOUT if body.dry_run else _BOOKING_TIMEOUT
    try:
        status_code, payload = await eva_client.post(
            "/admin/panel/appointments", body.model_dump(), timeout=timeout)
    except eva_client.EvaTimeout:
        if body.dry_run:
            raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
        raise HTTPException(
            status_code=504,
            detail="A Eva demorou a responder. Confira a lista de consultas antes de tentar de novo.")
    except eva_client.EvaUnavailable:
        raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
    if status_code in (401, 403):
        raise HTTPException(status_code=503, detail="Painel sem acesso à Eva (configuração). Nada foi alterado.")
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_new_appointment", body.phone, {
            "patient_id": body.patient_id, "agent": body.agent, "encaixe": body.encaixe_confirmed,
            "appointments": [a.get("appointment_id") for a in payload.get("appointments", [])],
        })
    return JSONResponse(status_code=status_code, content=payload)
