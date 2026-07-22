"""Rotas do painel da atendente (Fase 1).

Auth por token na query string (`?token=...`), validado contra
ATTENDANT_PANEL_TOKEN. As rotas existentes do dashboard mantêm o HTTP Basic;
estas usam o token (mais limpo dentro de um iframe do Chatwoot).
"""
import logging
import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

import attendant_db
import chatwoot_client
import payments
from db_client import get_client

router = APIRouter(prefix="/api/atendente")
logger = logging.getLogger(__name__)


def verify_token(token: str = Query(default="")) -> None:
    expected = os.getenv("ATTENDANT_PANEL_TOKEN", "")
    if not expected or not compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")


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
    return {"patient": patient, "link": link}


# ── Escrita ───────────────────────────────────────────────────────────────────


@router.post("/contato/{contact_id}")
async def update_contato(contact_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await attendant_db.update_contact(contact_id, body.data)
    await attendant_db.log_event("attendant_edit_contact", body.phone,
                                 {"contact_id": contact_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/paciente/{patient_id}")
async def update_paciente(patient_id: str, body: UpdateBody, _: None = Depends(verify_token)):
    await attendant_db.update_patient(patient_id, body.data)
    await attendant_db.log_event("attendant_edit_patient", body.phone,
                                 {"patient_id": patient_id, "fields": list(body.data.keys())})
    return {"ok": True}


@router.post("/vinculo/{pc_id}")
async def update_vinculo(pc_id: str, body: UpdateBody, _: None = Depends(verify_token)):
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
        drive_link = await payments.upload_comprovante(paciente, data_hora, valor, content, mimetype)
    except Exception:
        logger.exception("UPLOAD_COMPROVANTE_FAILED paciente=%s", paciente)
        raise HTTPException(status_code=502, detail="Falha ao enviar comprovante ao Drive")
    return {"drive_link": drive_link}


@router.post("/pagamentos/{appointment_id}/pagar")
async def pagar(appointment_id: str, body: AtendentePagarBody, _: None = Depends(verify_token)):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    client = await get_client()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
        drive_link=body.drive_link,
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
