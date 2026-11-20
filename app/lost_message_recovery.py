"""Rede de segurança contra mensagem muda perdida no buffer.

O buffer (app/buffer.py) é em memória: a mensagem do paciente é gravada em
`messages` no webhook, mas o processamento pelo grafo é agendado por um timer
(`loop.call_later`). Se o processo reinicia ou o timer se perde nessa janela, o
turno some — a mensagem fica no banco sem resposta, sem erro, sem evento, e a Eva
só reage quando a clínica manda uma nota privada (caso Marina Leal Ribeiro,
+5581988197076, 12/11/2026; memory mensagem-perdida-buffer-nota-privada-destrava).

Esta é a rede: uma tarefa de fundo (iniciada no lifespan) varre periodicamente as
últimas mensagens de paciente sem resposta e reprocessa pelo caminho normal. É
in-process de propósito — `process_message` depende do grafo com o checkpointer do
Postgres (montado no lifespan) e dos locks de telefone em memória; num subprocesso
a Eva trataria toda conversa como nova. As travas que impedem resposta dobrada e
intromissão em conversa pausada já vivem dentro do `process_message` (dedup de
checkpoint e portão de pausa), então aqui só reempurramos.

Complementa `_recover_messages_lost_to_restart` (só no boot, 10 min) e o drain de
shutdown; esta roda o tempo todo e pega a perda de qualquer causa.
"""
import asyncio
import contextlib
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

LOST_MSG_SWEEP_FLOOR_MIN = float(os.getenv("LOST_MSG_SWEEP_FLOOR_MIN", "5"))
LOST_MSG_SWEEP_INTERVAL_MIN = float(os.getenv("LOST_MSG_SWEEP_INTERVAL_MIN", "2"))
LOST_MSG_SWEEP_WINDOW_MIN = float(os.getenv("LOST_MSG_SWEEP_WINDOW_MIN", "360"))

REPLAY_EVENT = "lost_message_replayed"


def pick_candidates(rows: list[dict], now: datetime, floor_min: float) -> list[dict]:
    """Dentre as linhas de `messages`, devolve, por telefone, a ÚLTIMA linha quando
    ela é uma mensagem do paciente sem resposta posterior e velha o bastante.

    `rows`: linhas com `phone` (bare), `role`, `content`, `created_at` (ISO), em
    qualquer ordem. Candidato = última linha do telefone com `role == 'user'`,
    `content` não-vazio e `created_at <= now - floor_min`. Devolve
    `{"phone", "content", "created_at"}` por candidato.
    """
    cutoff = now - timedelta(minutes=floor_min)
    last_per_phone: dict[str, dict] = {}
    for row in rows:
        phone = row.get("phone")
        if not phone:
            continue
        prev = last_per_phone.get(phone)
        if prev is None or row["created_at"] > prev["created_at"]:
            last_per_phone[phone] = row

    candidates: list[dict] = []
    for phone, row in last_per_phone.items():
        if row.get("role") != "user":
            continue
        content = row.get("content")
        if not content:
            continue
        if datetime.fromisoformat(row["created_at"]) > cutoff:
            continue
        candidates.append({"phone": phone, "content": content, "created_at": row["created_at"]})
    return candidates


async def _already_replayed(full_phone: str, message_created_at: str) -> bool:
    """True se já reprocessamos esta (ou uma posterior) mensagem deste telefone —
    evita reempurrar duas vezes antes de a resposta cair. Confere se existe evento
    `lost_message_replayed` para o telefone com `created_at >= message_created_at`."""
    from app.database import get_supabase
    from app.phone import _strip_phone

    client = await get_supabase()
    r = (
        await client.from_("events").select("id")
        # log_event grava _strip_phone(phone); consultar com a mesma forma (dígitos
        # puros), senão o filtro nunca casa e a guarda vira código morto.
        .eq("phone", _strip_phone(full_phone)).eq("event_type", REPLAY_EVENT)
        .gte("created_at", message_created_at)
        .limit(1).execute()
    )
    return bool(r.data)


async def sweep(
    now: datetime,
    *,
    floor_min: float = LOST_MSG_SWEEP_FLOOR_MIN,
    window_min: float = LOST_MSG_SWEEP_WINDOW_MIN,
) -> int:
    """Reprocessa a última mensagem de paciente sem resposta de cada telefone na
    janela `[now - window_min, now]` e mais velha que `floor_min`. Reusa as travas
    do `process_message` (portão de pausa + dedup de checkpoint), então conversa
    pausada/transferida ou mensagem já respondida viram no-op. Retorna quantas
    reprocessou."""
    # Import local: app.main importa este módulo indiretamente (evita ciclo).
    from app.database import get_supabase, log_event
    from app.main import process_message

    lower = (now - timedelta(minutes=window_min)).isoformat()
    upper = now.isoformat()
    client = await get_supabase()
    res = (
        await client.from_("messages").select("phone, role, content, created_at")
        .gte("created_at", lower).lte("created_at", upper)
        .order("created_at").execute()
    )
    candidates = pick_candidates(res.data or [], now, floor_min)

    replayed = 0
    for cand in candidates:
        # Um candidato ruim nunca pode derrubar a rodada e perder os demais.
        try:
            full_phone = f"{cand['phone']}@s.whatsapp.net"
            if await _already_replayed(full_phone, cand["created_at"]):
                continue
            logger.warning(
                "LOST_MSG_SWEEP reprocessing unanswered message for %s (sent %s): %.60s",
                full_phone, cand["created_at"], cand["content"],
            )
            await process_message(full_phone, cand["content"])
            await log_event(REPLAY_EVENT, full_phone, {
                "message_created_at": cand["created_at"],
                "preview": cand["content"][:80],
            })
            replayed += 1
        except Exception:
            logger.exception("LOST_MSG_SWEEP_CANDIDATE_FAILED phone=%s", cand.get("phone"))
            continue

    if replayed:
        logger.warning("LOST_MSG_SWEEP done candidates=%d replayed=%d", len(candidates), replayed)
    return replayed


async def sweep_loop(
    interval_min: float = LOST_MSG_SWEEP_INTERVAL_MIN,
    *,
    _sleep=asyncio.sleep,
) -> None:
    """Laço da tarefa de fundo: varre, dorme, repete. Uma exceção do `sweep` nunca
    derruba o laço (fica logada); só o cancelamento (shutdown) encerra."""
    while True:
        try:
            await sweep(datetime.now(timezone.utc))
        except Exception:
            logger.exception("LOST_MSG_SWEEP round failed")
        await _sleep(interval_min * 60)


def start_lost_message_sweep() -> "asyncio.Task | None":
    """Inicia a tarefa de fundo da varredura, só em produção. Usa o MESMO gate do
    cron interno (`SCHEDULER_ENABLED=1`, definido no Dockerfile), para não disparar
    em dev/`uvicorn --reload` e sair respondendo paciente da máquina local. Devolve
    a Task, ou None."""
    if os.getenv("SCHEDULER_ENABLED") != "1":
        logger.info("Lost-message sweep disabled (SCHEDULER_ENABLED != 1)")
        return None
    logger.info("Starting lost-message sweep (every %.0f min)", LOST_MSG_SWEEP_INTERVAL_MIN)
    return asyncio.create_task(sweep_loop())


async def stop_lost_message_sweep(task: "asyncio.Task | None") -> None:
    """Cancela e aguarda a tarefa de fundo no shutdown. Tolera já estar parada."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
