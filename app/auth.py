"""
One-time Google OAuth flow to generate GOOGLE_REFRESH_TOKEN.

Usage (after deploying to VPS):
  1. Visit https://psiqueai.ayexa.com.br/auth/start?secret=AUTH_SECRET
  2. Authorize in the browser
  3. Copy GOOGLE_REFRESH_TOKEN from the response and add to .env
  4. Restart the server
"""
import os
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from google_auth_oauthlib.flow import Flow

REDIRECT_URI = "https://psiqueai.ayexa.com.br/auth/callback"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]

router = APIRouter(prefix="/auth", tags=["auth"])

# Reuse the same Flow object across /start and /callback (PKCE code_verifier lives here)
_flow_store: dict[str, Flow] = {}


def _build_flow() -> Flow:
    client_config = {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }
    return Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=REDIRECT_URI)


@router.get("/start")
async def auth_start(secret: str = Query(...)):
    """Initiate the Google OAuth flow. Pass ?secret=AUTH_SECRET to authorize."""
    if secret != os.environ.get("AUTH_SECRET", ""):
        raise HTTPException(status_code=403, detail="Invalid secret.")

    flow = _build_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    _flow_store["flow"] = flow  # persist so callback reuses same instance
    return RedirectResponse(auth_url)


@router.get("/callback")
async def auth_callback(request: Request):
    """Google redirects here after authorization."""
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code.")

    flow = _flow_store.get("flow")
    if not flow:
        raise HTTPException(status_code=400, detail="Flow not found. Start the auth flow again at /auth/start.")

    try:
        flow.fetch_token(code=code)
    except Warning:
        pass  # Google returns extra pre-authorized scopes; safe to ignore

    refresh_token = flow.credentials.refresh_token
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token returned. Revoke app access on Google and try again.",
        )

    html = f"""
    <html><body style="font-family:monospace;padding:2rem">
    <h2>✅ Autorização concluída!</h2>
    <p>Adicione ao <code>.env</code> do servidor e reinicie:</p>
    <pre style="background:#f4f4f4;padding:1rem;border-radius:8px">GOOGLE_REFRESH_TOKEN={refresh_token}</pre>
    <p style="color:#888">Você pode fechar esta aba.</p>
    </body></html>
    """
    return HTMLResponse(html)
