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
from pydantic import BaseModel

import attendant_db
import chatwoot_client
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


class ResetBody(BaseModel):
    phone: str


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
    return {"patient": patient, "link": link, "return_reminder": return_reminder}


# ── Escrita ───────────────────────────────────────────────────────────────────


@router.post("/contato/{contact_id}")
async def update_contato(contact_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_contact_scope(body.phone, contact_id)
    await attendant_db.update_contact(contact_id, body.data)
    await attendant_db.log_event("attendant_edit_contact", body.phone,
                                 {"contact_id": contact_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/paciente/{patient_id}")
async def update_paciente(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, patient_id)
    await attendant_db.update_patient(patient_id, body.data)
    await attendant_db.log_event("attendant_edit_patient", body.phone,
                                 {"patient_id": patient_id, "fields": list(body.data.keys())})
    return {"ok": True}


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
    await attendant_db.update_link(pc_id, body.data)
    await attendant_db.log_event("attendant_edit_link", body.phone,
                                 {"pc_id": pc_id, "fields": list(body.data.keys())})
    return {"ok": True}


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
