import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from secrets import compare_digest

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from supabase import AsyncClient, acreate_client

from app.google_sheets import append_payment_receipt
from app.email_sender import send_clinic_notification_email

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


# ── Pagamentos ────────────────────────────────────────────────────────────────

DOCTOR_DISPLAY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "Dr. Júlio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "Dra. Bruna",
}

DOCTOR_KEY = {
    "d5baa58b-a788-4f40-b8c0-512c189150be": "julio",
    "18b01f87-eacd-4905-bd4a-a8293991e6fd": "bruna",
}

FORMA_PAGAMENTO_LABEL = {
    "PIX": "PIX",
    "cartao_credito": "Cartão de crédito",
    "cartao_debito": "Cartão de débito",
    "dinheiro": "Dinheiro",
}


def _calc_valor_consulta(
    doctor_id: str,
    birth_date: str | None,
    consultation_type: str | None,
    custom_price: int | None,
) -> int:
    """Retorna o valor sugerido da consulta (com desconto de R$50 para dinheiro/PIX)."""
    if custom_price is not None:
        return custom_price
    age = None
    if birth_date:
        try:
            bd = date.fromisoformat(birth_date)
            today = date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except ValueError:
            pass

    doctor_key = DOCTOR_KEY.get(doctor_id, "")
    post_june = (date.today().year, date.today().month) >= (2026, 6)

    if doctor_key == "bruna":
        base = 700 if post_june else 600
    elif doctor_key == "julio":
        if age is None or age >= 18:
            base = 700 if post_june else 600
        elif consultation_type == "primeira_consulta":
            base = 850 if post_june else 750
        else:
            base = 750 if post_june else 650
    else:
        base = 700 if post_june else 600

    return base - 50  # desconto PIX/dinheiro


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


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request, username: str = Depends(verify_credentials)):
    return templates.TemplateResponse(request, "index.html", {"username": username})


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

    result = await (
        client.from_("appointments")
        .select(
            "appointment_id, start_time, doctor_id, paid_at, "
            "booking_fee_paid_at, booking_fee_waived, consultation_type, status, "
            "users(patient_name, name, birth_date, custom_price, number)"
        )
        .in_("status", ["scheduled", "completed"])
        .execute()
    )

    pendencias = []
    for appt in result.data or []:
        user = appt.get("users") or {}
        patient_name = user.get("patient_name") or user.get("name") or "Paciente"
        phone = user.get("number") or ""
        doctor_display = DOCTOR_DISPLAY.get(appt.get("doctor_id", ""), "Médico")
        start_time = appt.get("start_time", "")
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            dt_br = dt.astimezone(timezone(timedelta(hours=-3)))
            data_hora = dt_br.strftime("%d/%m/%Y %H:%M")
        except Exception:
            data_hora = start_time[:16]

        if not appt.get("booking_fee_paid_at") and not appt.get("booking_fee_waived"):
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "tipo": "taxa",
                "tipo_label": "Taxa de reserva",
                "valor": 100,
            })

        if not appt.get("paid_at"):
            valor = _calc_valor_consulta(
                appt.get("doctor_id", ""),
                user.get("birth_date"),
                appt.get("consultation_type"),
                user.get("custom_price"),
            )
            pendencias.append({
                "appointment_id": appt["appointment_id"],
                "paciente": patient_name,
                "phone": phone,
                "medico": doctor_display,
                "data_hora": data_hora,
                "tipo": "consulta",
                "tipo_label": "Consulta",
                "valor": valor,
            })

    pendencias.sort(key=lambda x: x["data_hora"])
    return templates.TemplateResponse(
        request, "pagamentos.html", {"username": username, "pendencias": pendencias}
    )


from pydantic import BaseModel


class PagarBody(BaseModel):
    tipo: str            # "taxa" ou "consulta"
    valor: int
    forma_pagamento: str # "PIX", "cartao_credito", "cartao_debito", "dinheiro"
    paciente: str
    medico: str
    data_hora: str
    phone: str


@app.post("/api/pagamentos/{appointment_id}/pagar")
async def api_pagar(
    appointment_id: str,
    body: PagarBody,
    username: str = Depends(verify_credentials),
):
    if body.tipo not in ("taxa", "consulta"):
        raise HTTPException(status_code=400, detail="tipo deve ser 'taxa' ou 'consulta'")

    now = datetime.now(timezone.utc).isoformat()

    client = get_supabase()
    if body.tipo == "taxa":
        await client.from_("appointments").update({"booking_fee_paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "taxa_reserva"
    else:
        await client.from_("appointments").update({"paid_at": now}).eq("appointment_id", appointment_id).execute()
        payment_type = "consulta"

    forma_label = FORMA_PAGAMENTO_LABEL.get(body.forma_pagamento, body.forma_pagamento)
    amount_str = str(body.valor)

    try:
        await append_payment_receipt(
            patient_name=body.paciente,
            phone=body.phone,
            doctor_name=body.medico,
            appointment_dt=body.data_hora,
            amount=amount_str,
            drive_link="",
            payment_type=payment_type,
            payment_method_override=body.forma_pagamento,
        )
    except Exception:
        logger.exception("SHEETS_APPEND FAILED patient=%s", body.paciente)

    try:
        tipo_label = "Taxa de reserva" if body.tipo == "taxa" else "Consulta"
        await send_clinic_notification_email(
            subject=f"Pagamento registrado — {body.paciente}",
            body=(
                f"💰 Pagamento registrado pelo dashboard\n"
                f"Paciente: {body.paciente}\n"
                f"Médico: {body.medico}\n"
                f"Consulta: {body.data_hora}\n"
                f"Tipo: {tipo_label}\n"
                f"Valor: R$ {amount_str}\n"
                f"Forma: {forma_label}"
            ),
        )
    except Exception:
        logger.exception("EMAIL_FAILED patient=%s", body.paciente)

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
