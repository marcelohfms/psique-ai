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


# Templates aprovados na Meta (UTILITY, pt_BR), ver docs/whatsapp-templates.md.
# Fora da janela de 24h o WhatsApp descarta texto livre em silêncio, então a
# confirmação vai como template. Dentro dela vai o mesmo texto, como mensagem livre.
TEMPLATE_PAGAMENTO = {
    "taxa": "pagamento_taxa_recebido",
    "consulta": "pagamento_consulta_recebido",
}


def _first_name(name: str | None) -> str:
    return (name or "").strip().split(" ")[0] if (name or "").strip() else ""


def _format_brl(valor: int) -> str:
    """650 -> 'R$ 650,00'; 1200 -> 'R$ 1.200,00'."""
    return "R$ " + f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def payment_confirmation(tipo: str, valor: int, contact_first: str,
                         patient_first: str | None) -> tuple[str, dict[str, str], str]:
    """(nome do template, body_params, texto) da confirmação de pagamento.

    `patient_first` é None quando quem está na conversa é o próprio paciente.
    Quando é um terceiro (ex.: a mãe), a mensagem cita o paciente pelo nome.
    O texto é o corpo do template já preenchido: vai como mensagem livre dentro
    da janela e como `content` (registro no Chatwoot) fora dela."""
    valor_str = _format_brl(valor)
    if tipo == "taxa":
        consulta = f"a consulta de {patient_first}" if patient_first else "sua consulta"
        params = {"1": contact_first, "2": valor_str, "3": consulta}
        text = (
            f"Olá, {contact_first}! 😊\n"
            f"Passando apenas para confirmar o recebimento de {valor_str} referente à "
            f"sua taxa de reserva. Não se preocupe, {consulta} está garantida. Muito obrigada! 💜"
        )
    else:
        consulta = f"à consulta de {patient_first}" if patient_first else "à sua consulta"
        params = {"1": contact_first, "2": valor_str, "3": consulta}
        text = (
            f"Olá, {contact_first}! 😊\n"
            f"Passando apenas para confirmar o recebimento de {valor_str} referente "
            f"{consulta}. Está tudo certo agora, muito obrigada! 💜"
        )
    return TEMPLATE_PAGAMENTO[tipo], params, text


async def _confirmation_names(phone: str, paciente: str) -> tuple[str, str | None]:
    """(primeiro nome de quem está na conversa, primeiro nome do paciente ou None
    se for a mesma pessoa). Sem contato no banco, trata como o próprio paciente
    (comportamento antigo da confirmação)."""
    patient_first = _first_name(paciente)
    try:
        resolved = await attendant_db.resolve_contact_and_patients(phone)
    except Exception:
        logger.exception("CONFIRM_RESOLVE_FAILED phone=%s", phone)
        return patient_first, None
    contact = resolved.get("contact") or {}
    contact_first = _first_name(contact.get("name"))
    if not contact_first:
        return patient_first, None
    alvo = attendant_db._norm(paciente)
    for p in resolved.get("patients") or []:
        if attendant_db._norm(p.get("name")) == alvo:
            is_self = bool((p.get("link") or {}).get("is_self"))
            return contact_first, (None if is_self else patient_first)
    # Paciente não achado entre os vínculos: compara os nomes, como o lembrete de taxa.
    same = attendant_db._norm(contact_first) == attendant_db._norm(patient_first)
    return contact_first, (None if same else patient_first)


async def _send_via_eva(phone: str, text: str, template: str = "",
                        params: dict[str, str] | None = None) -> str:
    """Pede à Eva para mandar a mensagem ao contato. Ela tem as credenciais do
    Chatwoot que funcionam e decide entre texto livre (janela de 24h aberta) e
    template. Antes o dashboard postava direto no Chatwoot e nada saía.

    Devolve "enviada", "fora_da_janela" (sem template e contato calado há mais de
    24h) ou "falhou", para o painel avisar a atendente."""
    try:
        status_code, payload = await eva_client.post("/admin/panel/payment-confirmation", {
            "phone": phone, "template": template, "params": params or {}, "text": text,
        })
    except Exception:
        logger.exception("CONFIRM_MSG_FAILED phone=%s", phone)
        return "falhou"
    if status_code != 200:
        logger.error("CONFIRM_MSG_FAILED phone=%s status=%s payload=%s", phone, status_code, payload)
        return "falhou"
    return "enviada" if payload.get("sent") else "fora_da_janela"


async def _send_payment_confirmation(phone: str, tipo: str, valor: int, paciente: str) -> str:
    contact_first, patient_first = await _confirmation_names(phone, paciente)
    template, params, text = payment_confirmation(tipo, valor, contact_first, patient_first)
    return await _send_via_eva(phone, text, template, params)


def waiver_text(contact_first: str, patient_first: str | None, medico: str) -> str:
    consulta = f"a consulta de {patient_first}" if patient_first else "sua consulta"
    return (
        f"Olá, {contact_first}! 😊\n"
        f"A taxa de reserva para {consulta} com {medico} foi isentada. "
        f"Não é necessário nenhum pagamento antecipado."
    )


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


async def _registrar_pagamento(appointment_id: str, body: AtendentePagarBody) -> str:
    """Grava o pagamento e manda a confirmação ao contato. Devolve o status do
    envio (ver _send_via_eva) para o painel avisar quando a mensagem não saiu."""
    client = await get_client()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
        drive_link=body.drive_link,
        receipt_filename=body.receipt_filename,
    )

    try:
        confirmacao = await _send_payment_confirmation(body.phone, body.tipo, body.valor, body.paciente)
    except Exception:
        confirmacao = "falhou"
        logger.exception("CONFIRM_MSG_FAILED appt=%s phone=%s", appointment_id, body.phone)

    # O pagamento já está gravado aqui; uma falha só no log não pode virar
    # "NÃO registrado" para a atendente, senão ela tenta de novo e duplica.
    try:
        await attendant_db.log_event("attendant_pagamento_registrado", body.phone, {
            "appointment_id": appointment_id, "tipo": body.tipo, "valor": body.valor,
        })
    except Exception:
        logger.exception("LOG_EVENT_FAILED appt=%s", appointment_id)
    return confirmacao


@router.post("/pagamentos/{appointment_id}/pagar")
async def pagar(appointment_id: str, body: AtendentePagarBody, _: None = Depends(verify_token)):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    await _assert_appointment_scope(body.phone, appointment_id)
    confirmacao = await _registrar_pagamento(appointment_id, body)
    return {"ok": True, "confirmacao": confirmacao}


@router.post("/pagamentos/{appointment_id}/pagar-com-comprovante")
async def pagar_com_comprovante(
    appointment_id: str,
    tipo: str = Form(...),
    valor: int = Form(...),
    forma_pagamento: str = Form(...),
    paciente: str = Form(...),
    medico: str = Form(...),
    data_hora: str = Form(...),
    phone: str = Form(...),
    conversation_id: int | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    _: None = Depends(verify_token),
):
    """Sobe o comprovante (se houver) e registra o pagamento numa única requisição.

    Antes eram duas chamadas do navegador (/comprovante e depois /pagar). Se a
    segunda se perdia, o arquivo ficava no Drive mas a taxa seguia em aberto, sem
    linha na planilha, e o cron cobrava o paciente (caso Bento/Juliana, 05/10/2026).
    Agora o servidor faz as duas coisas e, se o registro falhar depois do upload,
    responde com erro dizendo exatamente isso e deixa um evento para auditoria.
    """
    if tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    await _assert_appointment_scope(phone, appointment_id)

    drive_link, receipt_filename = "", ""
    if file is not None and file.filename:
        content = await file.read()
        mimetype = file.content_type or "image/jpeg"
        try:
            drive_link, receipt_filename = await payments.upload_comprovante(
                paciente, data_hora, str(valor), content, mimetype,
            )
        except Exception:
            logger.exception("UPLOAD_COMPROVANTE_FAILED appt=%s paciente=%s", appointment_id, paciente)
            raise HTTPException(
                status_code=502,
                detail="Falha ao enviar o comprovante ao Drive. Nada foi registrado; tente de novo.",
            )

    body = AtendentePagarBody(
        tipo=tipo, valor=valor, forma_pagamento=forma_pagamento, paciente=paciente,
        medico=medico, data_hora=data_hora, phone=phone, conversation_id=conversation_id,
        drive_link=drive_link, receipt_filename=receipt_filename,
    )
    try:
        confirmacao = await _registrar_pagamento(appointment_id, body)
    except Exception:
        logger.exception("ATTENDANT_PAGAMENTO_FAILED appt=%s drive_link=%s", appointment_id, drive_link)
        try:
            await attendant_db.log_event("attendant_pagamento_falhou", phone, {
                "appointment_id": appointment_id, "tipo": tipo, "valor": valor,
                "drive_link": drive_link,
            })
        except Exception:
            logger.exception("LOG_EVENT_FAILED appt=%s", appointment_id)
        detail = (
            "O comprovante foi salvo no Drive, mas o pagamento NÃO foi registrado. Tente de novo."
            if drive_link else "O pagamento NÃO foi registrado. Tente de novo."
        )
        raise HTTPException(status_code=500, detail=detail)
    return {"ok": True, "confirmacao": confirmacao}


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

    try:
        contact_first, patient_first = await _confirmation_names(body.phone, body.paciente)
        confirmacao = await _send_via_eva(body.phone, waiver_text(contact_first, patient_first, body.medico))
    except Exception:
        confirmacao = "falhou"
        logger.exception("CONFIRM_MSG_FAILED appt=%s phone=%s", appointment_id, body.phone)

    try:
        await attendant_db.log_event("attendant_taxa_isentada", body.phone, {
            "appointment_id": appointment_id,
        })
    except Exception:
        logger.exception("LOG_EVENT_FAILED appt=%s", appointment_id)
    return {"ok": True, "confirmacao": confirmacao}


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
    doctor_id = await attendant_db.get_appointment_doctor_id(appointment_id)
    if attendant_db._DOCTOR_KEY.get(doctor_id) != "julio":
        raise HTTPException(status_code=400,
                            detail="a etiqueta 1ª consulta só vale para menor com o Dr. Júlio")
    await attendant_db.set_first_consultation(appointment_id, body.first)
    await attendant_db.log_event("attendant_first_consultation", body.phone,
                                 {"appointment_id": appointment_id, "first": body.first, "agent": body.agent})
    return {"ok": True}


_DRY_RUN_TIMEOUT = 20.0
_BOOKING_TIMEOUT = 60.0


async def _forward_to_eva(path: str, payload: dict, dry_run: bool) -> tuple[int, dict]:
    """Chama a Eva e traduz as falhas para a atendente. Em dry_run nada é gravado,
    então timeout vira "nada foi alterado"; fora dele, a Eva pode ter concluído."""
    timeout = _DRY_RUN_TIMEOUT if dry_run else _BOOKING_TIMEOUT
    try:
        status_code, body = await eva_client.post(path, payload, timeout=timeout)
    except eva_client.EvaTimeout:
        if dry_run:
            raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
        raise HTTPException(
            status_code=504,
            detail="A Eva demorou a responder. Confira a lista de consultas antes de tentar de novo.")
    except eva_client.EvaUnavailable:
        raise HTTPException(status_code=503, detail="A Eva não respondeu. Nada foi alterado.")
    if status_code in (401, 403):
        raise HTTPException(status_code=503, detail="Painel sem acesso à Eva (configuração). Nada foi alterado.")
    return status_code, body


@router.post("/consulta/nova")
async def nova_consulta(body: NewAppointmentBody, _: None = Depends(verify_token)):
    await _assert_patient_scope(body.phone, body.patient_id)
    status_code, payload = await _forward_to_eva("/admin/panel/appointments", body.model_dump(), body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_new_appointment", body.phone, {
            "patient_id": body.patient_id, "agent": body.agent, "encaixe": body.encaixe_confirmed,
            "appointments": [a.get("appointment_id") for a in payload.get("appointments", [])],
        })
    return JSONResponse(status_code=status_code, content=payload)


class EditAppointmentBody(BaseModel):
    phone: str
    doctor: str
    modality: str
    start: str
    minutes: int
    session_note: str = Field(default="", max_length=80)
    first_consultation: bool = False
    billing: str = "normal"
    initiated_by: str | None = None
    encaixe_confirmed: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


class CancelAppointmentBody(BaseModel):
    phone: str
    initiated_by: str | None = None
    fee_action: str | None = None
    reason: str = Field(default="", max_length=200)
    both_parts: bool = False
    dry_run: bool = False
    agent: str = Field(default="", max_length=80)


@router.post("/consulta/{appointment_id}/alterar")
async def alterar_consulta(appointment_id: str, body: EditAppointmentBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    payload = {**body.model_dump(), "appointment_id": appointment_id}
    status_code, resp = await _forward_to_eva("/admin/panel/appointments/edit", payload, body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_edit_appointment", body.phone, {
            "appointment_id": appointment_id, "new_appointment_id": resp.get("appointment_id"),
            "agent": body.agent, "encaixe": body.encaixe_confirmed, "initiated_by": body.initiated_by,
        })
    return JSONResponse(status_code=status_code, content=resp)


@router.post("/consulta/{appointment_id}/cancelar")
async def cancelar_consulta(appointment_id: str, body: CancelAppointmentBody, _: None = Depends(verify_token)):
    await _assert_appointment_scope(body.phone, appointment_id)
    payload = {**body.model_dump(), "appointment_id": appointment_id}
    status_code, resp = await _forward_to_eva("/admin/panel/appointments/cancel", payload, body.dry_run)
    if status_code == 200 and not body.dry_run:
        await attendant_db.log_event("attendant_cancel_appointment", body.phone, {
            "appointment_id": appointment_id, "canceled": resp.get("canceled", []), "agent": body.agent,
            "initiated_by": body.initiated_by, "fee_action": body.fee_action,
        })
    return JSONResponse(status_code=status_code, content=resp)
