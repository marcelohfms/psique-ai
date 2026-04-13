import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from secrets import compare_digest

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
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


async def get_supabase() -> AsyncClient:
    global _supabase
    if _supabase is None:
        _supabase = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
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
            client = await get_supabase()
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


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    client = await get_supabase()

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
    client = await get_supabase()
    result = (
        await client.from_("messages")
        .select("*")
        .eq("phone", phone)
        .order("created_at")
        .execute()
    )
    return result.data or []


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep-alive; ignore client messages
    except WebSocketDisconnect:
        manager.disconnect(ws)
