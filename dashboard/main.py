import asyncio
import base64
import binascii
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from secrets import compare_digest

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from supabase import AsyncClient, acreate_client

logger = logging.getLogger(__name__)

# ── Auth ──────────────────────────────────────────────────────────────────────

security = HTTPBasic()
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

# Senhas que NÃO protegem nada: vazio (variável esquecida no deploy) ou o antigo
# default "changeme". Com qualquer uma delas o painel recusa todo mundo (fail
# closed) em vez de liberar o acesso com uma senha pública. Melhor ninguém entrar
# do que qualquer um entrar.
_INSECURE_PASSWORDS = {"", "changeme"}


def _password_configured() -> bool:
    return DASHBOARD_PASSWORD not in _INSECURE_PASSWORDS


if not _password_configured():
    logger.warning(
        "DASHBOARD_PASSWORD não configurado (ou ainda no default inseguro) — "
        "o painel vai recusar TODAS as autenticações até uma senha real ser setada."
    )


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct = _password_configured() and compare_digest(
        credentials.password.encode(), DASHBOARD_PASSWORD.encode()
    )
    if not correct:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ── Supabase ──────────────────────────────────────────────────────────────────

_supabase: AsyncClient | None = None


async def _init_supabase() -> None:
    global _supabase
    if _supabase is None:
        _supabase = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )


def get_supabase() -> AsyncClient:
    if _supabase is None:
        raise RuntimeError("Supabase client not initialized")
    return _supabase


# ── WebSocket connection manager ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, data: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in self.active:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.active -= dead


manager = ConnectionManager()


# ── Background polling ────────────────────────────────────────────────────────

async def _poll_new_messages() -> None:
    """Poll Supabase every 1.5 s for new messages and broadcast to all WS clients."""
    global _supabase
    last_ts = datetime.now(timezone.utc).isoformat()
    while True:
        await asyncio.sleep(1.5)
        try:
            client = get_supabase()
            result = (
                await client.from_("messages")
                .select("*")
                .gt("created_at", last_ts)
                .order("created_at")
                .execute()
            )
            if result.data:
                last_ts = result.data[-1]["created_at"]
                for msg in result.data:
                    await manager.broadcast({"type": "new_message", "message": msg})
        except Exception:
            logger.exception("Polling error")
            _supabase = None
            try:
                await _init_supabase()
            except Exception:
                pass  # retry on next poll cycle


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Um deploy sem SMTP grita aqui, no boot, em vez de esperar o próximo
    # pagamento passar despercebido pela clínica (ver payments._send_clinic_email).
    faltando = payments.missing_smtp_vars()
    if faltando:
        logger.error(
            "SMTP INCOMPLETO no serviço dashboard — variáveis ausentes: %s. "
            "Nenhum e-mail de pagamento chegará à clínica até que sejam configuradas.",
            ", ".join(faltando),
        )
    await _init_supabase()
    task = asyncio.create_task(_poll_new_messages())
    yield
    task.cancel()


# ── FastAPI ───────────────────────────────────────────────────────────────────

app = FastAPI(title="Psique Dashboard", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

import attendant_routes
import payments
import return_reminders

app.include_router(attendant_routes.router)

ATTENDANT_PANEL_TOKEN = os.getenv("ATTENDANT_PANEL_TOKEN", "")
_FRAME_ANCESTOR = os.getenv("CHATWOOT_FRAME_ANCESTOR", "'self'")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Cabeçalhos de segurança em toda resposta do painel.

    frame-ancestors: permite que o Chatwoot embuta o painel num iframe.
    Referrer-Policy no-referrer: o token do painel viaja na URL do iframe; sem
      isso ele vazaria no header Referer de qualquer requisição saindo da página.
    nosniff: impede o navegador de adivinhar o content-type (anti-XSS).
    HSTS: força HTTPS nas próximas visitas.
    """
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {_FRAME_ANCESTOR}"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if "x-frame-options" in response.headers:  # CSP é a fonte da verdade
        del response.headers["x-frame-options"]
    return response


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request, username: str = Depends(verify_credentials)):
    return templates.TemplateResponse(request, "index.html", {"username": username})


def _valid_panel_token(token: str) -> bool:
    """Tempo constante; falha fechado quando o token não está configurado."""
    if not ATTENDANT_PANEL_TOKEN:
        return False
    return compare_digest(token.encode(), ATTENDANT_PANEL_TOKEN.encode())


@app.get("/atendente")
async def atendente_page(request: Request, token: str = ""):
    # A página injeta o token no JS pras chamadas /api/atendente. O Chatwoot já
    # abre o iframe com ?token=... (ver cabeçalho de atendente.html), então
    # exigir o token aqui não quebra o embed e impede que um visitante anônimo
    # receba o segredo de graça.
    if not _valid_panel_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")
    return templates.TemplateResponse(request, "atendente.html", {"token": ATTENDANT_PANEL_TOKEN})


@app.get("/api/conversations")
async def api_conversations(username: str = Depends(verify_credentials)):
    client = get_supabase()

    # Fetch recent messages to derive one entry per phone
    result = (
        await client.from_("messages")
        .select("phone, content, role, created_at")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
    )

    # Dedup: keep the most recent message per phone
    seen: dict[str, dict] = {}
    for msg in result.data or []:
        if msg["phone"] not in seen:
            seen[msg["phone"]] = msg

    if not seen:
        return []

    # Fetch names from users table
    phones = list(seen.keys())
    users_result = (
        await client.from_("users")
        .select("number, name")
        .in_("number", phones)
        .execute()
    )
    user_names = {u["number"]: u["name"] for u in (users_result.data or [])}

    conversations = [
        {
            "phone": phone,
            "name": user_names.get(phone) or phone,
            "last_message": msg["content"],
            "last_role": msg["role"],
            "last_at": msg["created_at"],
        }
        for phone, msg in seen.items()
    ]
    conversations.sort(key=lambda x: x["last_at"], reverse=True)
    return conversations


@app.get("/api/messages/{phone}")
async def api_messages(phone: str, username: str = Depends(verify_credentials)):
    client = get_supabase()
    result = (
        await client.from_("messages")
        .select("*")
        .eq("phone", phone)
        .order("created_at")
        .execute()
    )
    return result.data or []


@app.get("/pagamentos")
async def pagamentos_page(request: Request, username: str = Depends(verify_credentials)):
    client = get_supabase()
    pendencias = await payments.compute_pendencias(client)
    return templates.TemplateResponse(
        request, "pagamentos.html", {"username": username, "pendencias": pendencias}
    )


class PagarBody(BaseModel):
    tipo: str            # "taxa" ou "consulta"
    valor: int
    forma_pagamento: str # "PIX", "cartao_credito", "cartao_debito", "dinheiro"
    paciente: str
    medico: str
    data_hora: str
    phone: str
    drive_link: str = ""  # link do comprovante já enviado ao Drive (ver /pagamentos/{id}/comprovante)
    receipt_filename: str = ""  # nome do arquivo no Drive, devolvido pela mesma rota


@app.post("/api/pagamentos/{appointment_id}/comprovante")
async def api_upload_comprovante(
    appointment_id: str,
    paciente: str = Form(...),
    data_hora: str = Form(...),
    valor: str = Form(...),
    file: UploadFile = File(...),
    username: str = Depends(verify_credentials),
):
    content = await file.read()
    mimetype = file.content_type or "image/jpeg"
    try:
        drive_link, filename = await payments.upload_comprovante(paciente, data_hora, valor, content, mimetype)
    except Exception:
        logger.exception("UPLOAD_COMPROVANTE_FAILED appt=%s paciente=%s", appointment_id, paciente)
        raise HTTPException(status_code=502, detail="Falha ao enviar comprovante ao Drive")
    return {"drive_link": drive_link, "receipt_filename": filename}


class RetornoBody(BaseModel):
    doctor_id: str
    appointment_id: str
    appointment_date: date
    return_interval: str


@app.get("/retornos")
async def retornos_page(request: Request, medico: str = "julio", username: str = Depends(verify_credentials)):
    client = get_supabase()
    if medico not in return_reminders.DOCTOR_ID_BY_KEY:
        medico = "julio"
    doctor_id = return_reminders.DOCTOR_ID_BY_KEY[medico]
    hoje = await return_reminders.get_today_appointments(client, doctor_id)
    pendentes = await return_reminders.get_pending_classification(client, doctor_id)
    return templates.TemplateResponse(request, "retornos.html", {
        "username": username,
        "medico": medico,
        "hoje": hoje,
        "pendentes": pendentes,
        "intervalos": return_reminders.RETURN_INTERVAL_LABELS,
        "medico_doctor_id": doctor_id,
    })


@app.post("/api/retornos/{patient_id}")
async def api_salvar_retorno(patient_id: str, body: RetornoBody, username: str = Depends(verify_credentials)):
    if body.return_interval not in return_reminders.RETURN_INTERVALS:
        raise HTTPException(status_code=400, detail="return_interval inválido")
    client = get_supabase()
    saved = await return_reminders.save_classification(
        client, patient_id, body.doctor_id, body.appointment_id, body.appointment_date, body.return_interval,
    )
    return {"ok": True, "return_reminder": saved}


class AltaBody(BaseModel):
    doctor_id: str
    appointment_id: str


class NoShowBody(BaseModel):
    appointment_id: str


@app.post("/api/retornos/{patient_id}/alta")
async def api_alta(patient_id: str, body: AltaBody, username: str = Depends(verify_credentials)):
    client = get_supabase()
    saved = await return_reminders.save_discharge(
        client, patient_id, body.doctor_id, body.appointment_id,
    )
    return {"ok": True, "return_reminder": saved}


@app.post("/api/retornos/{patient_id}/no-show")
async def api_no_show(patient_id: str, body: NoShowBody, username: str = Depends(verify_credentials)):
    client = get_supabase()
    await return_reminders.mark_no_show(client, body.appointment_id)
    return {"ok": True}


@app.post("/api/pagamentos/{appointment_id}/pagar")
async def api_pagar(
    appointment_id: str,
    body: PagarBody,
    username: str = Depends(verify_credentials),
):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    client = get_supabase()
    await payments.mark_paid(
        client, appointment_id, body.tipo, body.valor, body.forma_pagamento,
        body.paciente, body.medico, body.data_hora, body.phone,
        drive_link=body.drive_link,
        receipt_filename=body.receipt_filename,
    )
    return {"ok": True}


@app.post("/api/pagamentos/{appointment_id}/no-show")
async def api_pagamentos_no_show(appointment_id: str, username: str = Depends(verify_credentials)):
    client = get_supabase()
    await return_reminders.mark_no_show(client, appointment_id)
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

def _ws_authorized(ws: WebSocket) -> bool:
    """Valida o HTTP Basic no handshake do WebSocket.

    O navegador reenvia a credencial Basic no handshake porque a página / foi
    aberta autenticada (mesma origem). Sem isso o /ws transmitia as mensagens de
    todos os pacientes para qualquer cliente anônimo.
    """
    header = ws.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    _, _, password = decoded.partition(":")
    return _password_configured() and compare_digest(
        password.encode(), DASHBOARD_PASSWORD.encode()
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    if not _ws_authorized(ws):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; ignore client messages
    except WebSocketDisconnect:
        manager.disconnect(ws)
