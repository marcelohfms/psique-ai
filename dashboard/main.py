import asyncio
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
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    correct = compare_digest(
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
async def _frame_headers(request: Request, call_next):
    """Permite que o Chatwoot embuta o painel num iframe (frame-ancestors)."""
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {_FRAME_ANCESTOR}"
    if "x-frame-options" in response.headers:  # CSP é a fonte da verdade
        del response.headers["x-frame-options"]
    return response


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request, username: str = Depends(verify_credentials)):
    return templates.TemplateResponse(request, "index.html", {"username": username})


@app.get("/atendente")
async def atendente_page(request: Request):
    # A auth real é por token nas chamadas /api/atendente; a página injeta o token no JS.
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


@app.get("/api/pagamentos/comprovantes")
async def api_buscar_comprovantes(phone: str, username: str = Depends(verify_credentials)):
    client = get_supabase()
    return await payments.find_receipts(client, phone)


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
        drive_link = await payments.upload_comprovante(paciente, data_hora, valor, content, mimetype)
    except Exception:
        logger.exception("UPLOAD_COMPROVANTE_FAILED appt=%s paciente=%s", appointment_id, paciente)
        raise HTTPException(status_code=502, detail="Falha ao enviar comprovante ao Drive")
    return {"drive_link": drive_link}


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
    )
    return {"ok": True}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; ignore client messages
    except WebSocketDisconnect:
        manager.disconnect(ws)
