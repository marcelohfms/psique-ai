"""Rotas do painel da atendente (Fase 1).

Auth por token na query string (`?token=...`), validado contra
ATTENDANT_PANEL_TOKEN. As rotas existentes do dashboard mantêm o HTTP Basic;
estas usam o token (mais limpo dentro de um iframe do Chatwoot).
"""
import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

import attendant_db

router = APIRouter(prefix="/api/atendente")


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
