"""Cliente mínimo de Google Calendar para o painel da atendente.

Autocontido: NÃO importa `app/` (a imagem Docker do dashboard não contém `app/`).
Espelha só o pedaço de `app/google_calendar.py` que marca eventos — os dois têm
que andar juntos. Usa as mesmas credenciais do app (GOOGLE_REFRESH_TOKEN /
GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET), que já têm escopo de calendar.
"""
import asyncio
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Marca de presença confirmada (verde + "✅") que o app põe no título quando o
# paciente confirma no lembrete de véspera (app/google_calendar.py). Numa falta
# a gente remove esse "✅" — é contraditório — e troca pela marca de falta.
CONFIRMED_PREFIX = "✅"

# Cor "Tomato" (vermelho) do Google Calendar + "❌ [Não compareceu]" no título:
# registro visual permanente da consulta em que o paciente não compareceu, pra
# clínica ver de relance no calendário.
NO_SHOW_COLOR_ID = "11"
NO_SHOW_PREFIX = "❌ [Não compareceu] "


def _credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/calendar"],
    )


def build_no_show_summary(summary: str) -> str:
    """Título do evento marcado como falta.

    Preserva o título existente (inclusive edições manuais da clínica), tira o
    "✅" de presença confirmada se houver e prefixa "❌ [Não compareceu]" uma
    única vez (idempotente: re-marcar não duplica o prefixo).
    """
    summary = (summary or "").strip()
    if summary.startswith(CONFIRMED_PREFIX):
        summary = summary[len(CONFIRMED_PREFIX):].lstrip()
    if not summary.startswith(NO_SHOW_PREFIX.strip()):
        summary = f"{NO_SHOW_PREFIX}{summary}".strip()
    return summary


def _mark_event_no_show(service, calendar_id: str, event_id: str) -> None:
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    patch = {
        "summary": build_no_show_summary(event.get("summary") or ""),
        "colorId": NO_SHOW_COLOR_ID,
    }
    service.events().patch(calendarId=calendar_id, eventId=event_id, body=patch).execute()


async def mark_event_no_show(calendar_id: str, event_id: str) -> None:
    """Pinta o evento de vermelho e prefixa "❌ [Não compareceu]" no título."""
    service = build("calendar", "v3", credentials=_credentials())
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _mark_event_no_show, service, calendar_id, event_id)
