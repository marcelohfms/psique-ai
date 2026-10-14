"""Agendador interno das rotinas (crons) que antes rodavam no GitHub Actions.

O GitHub descarta a maioria das rodadas agendadas: o */10 dos lembretes rodava de
2 em 2h, às vezes 5h de buraco (ver memória "lembretes atrasados"). A Eva fica
ligada 24/7 no Easypanel, então ela vira o relógio.

Como funciona:
- Um laço acorda a cada TICK_SECONDS e vê quais rotinas venceram.
- Cada rotina roda como SUBPROCESSO (`python -m scripts.<x>`), igual ao GitHub:
  os scripts usam chamadas bloqueantes (smtplib, cliente do Google) e não podem
  travar o event loop do webhook. Também isola falha e memória.
- Uma rotina por vez (fila única). A VPS é pequena e já travou antes; seis
  processos subindo juntos no :00 seria pedir para travar de novo.
- Rotinas de intervalo (a cada N min) guardam a última rodada só na memória: são
  idempotentes, rodar de novo depois de um deploy é inofensivo.
- Rotinas de horário fixo (diárias, semanais, mensais) NÃO são idempotentes
  (e-mail da agenda do médico, por exemplo). A rodada fica registrada na tabela
  `events` antes de começar, para um restart não repetir o envio. Se a leitura
  falhar, a rodada é pulada (prefere perder um e-mail a mandar dois).

Só liga com SCHEDULER_ENABLED=1, que o Dockerfile de produção define. Rodar a Eva
local (`uvicorn --reload`) ou os testes nunca dispara cron.

SCHEDULER_LIVE=False é o modo sombra: o agendador calcula e registra o que rodaria
(evento scheduler_shadow) mas não executa nada, enquanto o GitHub continua sendo
o dono das rotinas. Serve para conferir horários e variáveis de ambiente em
produção antes da virada.
"""
import asyncio
import logging
import os
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TZ = ZoneInfo("America/Recife")
TICK_SECONDS = 30
JOB_TIMEOUT_SECONDS = 15 * 60
SHUTDOWN_GRACE_SECONDS = 20
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Ao vivo: o agendador executa as rotinas. Os `schedule:` dos workflows do GitHub
# foram removidos na virada (2026-10-14); só o `workflow_dispatch` manual ficou.
SCHEDULER_LIVE = True

# Eventos (tabela events, phone fixo) para auditoria e trava das rodadas.
EVENT_PHONE = "agendador"
EV_BOOT = "scheduler_boot"
EV_RUN = "scheduler_run"
EV_SHADOW = "scheduler_shadow"
EV_FAILED = "scheduler_job_failed"

_SUPABASE = ("SUPABASE_URL", "SUPABASE_KEY")
_PG = ("SUPABASE_CONNECTION_STRING",)
_WHATSAPP = ("WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID")
_CHATWOOT = (
    "CHATWOOT_BASE_URL", "CHATWOOT_ACCOUNT_ID", "CHATWOOT_AGENT_BOT_TOKEN",
    "CHATWOOT_USER_TOKEN", "CHATWOOT_INBOX_ID",
)
_GOOGLE = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
_SMTP = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD")


@dataclass(frozen=True)
class Every:
    """A cada N minutos, alinhado ao relógio de Recife (N=30 → :00 e :30)."""
    minutes: int

    def slot(self, now: datetime) -> datetime:
        local = now.astimezone(TZ).replace(second=0, microsecond=0)
        minute_of_day = local.hour * 60 + local.minute
        return local - timedelta(minutes=minute_of_day % self.minutes)

    @property
    def grace(self) -> timedelta:
        return timedelta(minutes=self.minutes)

    persisted = False


@dataclass(frozen=True)
class At:
    """Horário fixo em Recife. weekdays: 0=segunda … 6=domingo. monthday: dia do
    mês. grace_hours: até quanto tempo depois do horário ainda vale rodar (cobre
    um restart bem na hora)."""
    hour: int
    minute: int = 0
    weekdays: tuple[int, ...] | None = None
    monthday: int | None = None
    grace_hours: float = 4

    def _matches(self, d: datetime) -> bool:
        if self.weekdays is not None and d.weekday() not in self.weekdays:
            return False
        if self.monthday is not None and d.day != self.monthday:
            return False
        return True

    def slot(self, now: datetime) -> datetime:
        local = now.astimezone(TZ)
        cand = local.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        if cand > local:
            cand -= timedelta(days=1)
        for _ in range(62):
            if self._matches(cand):
                return cand
            cand -= timedelta(days=1)
        raise ValueError(f"agenda sem ocorrência em 62 dias: {self}")

    @property
    def grace(self) -> timedelta:
        return timedelta(hours=self.grace_hours)

    persisted = True


@dataclass(frozen=True)
class Job:
    name: str
    module: str  # scripts.<module>
    schedule: Every | At
    env: tuple[str, ...] = field(default_factory=tuple)


# Espelho dos workflows de .github/workflows (mesmo horário, mesmas variáveis).
# Os horários do GitHub estavam em UTC; aqui já em Recife (UTC-3 fixo).
JOBS: tuple[Job, ...] = (
    Job("appointment_reminders", "send_appointment_reminders", Every(10),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT),
    Job("complete_appointments", "complete_appointments", Every(60),
        _SUPABASE + _WHATSAPP + _CHATWOOT),
    Job("payment_reminders", "send_payment_reminders", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE + _SMTP
        + ("CLINIC_NOTIFY_EMAIL",)),
    Job("release_pending_reschedules", "release_pending_reschedules", Every(30),
        _SUPABASE + _WHATSAPP + _CHATWOOT + _GOOGLE),
    Job("lead_stall_nudges", "send_lead_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE),
    Job("scheduling_stall_nudges", "send_scheduling_stall_nudges", Every(30),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT + _GOOGLE),
    Job("sync_contact_attributes", "sync_contact_attributes", Every(30),
        _SUPABASE + _CHATWOOT),
    Job("return_reminders", "send_return_reminders", At(8),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT),
    Job("scheduling_stall_report", "send_scheduling_stall_report", At(8),
        _SUPABASE + _SMTP + ("CLINIC_NOTIFY_EMAIL",)),
    Job("funnel_report", "send_funnel_report", At(8, weekdays=(0,)),
        _SUPABASE + _SMTP + ("FUNNEL_REPORT_EMAIL",)),
    Job("no_show_messages", "send_no_show_messages", At(9),
        _SUPABASE + _PG + _WHATSAPP + _CHATWOOT),
    Job("pending_payments_reminder", "send_pending_payments_reminder",
        At(9, weekdays=(0, 1, 2, 3, 4, 5)),
        _SUPABASE + _SMTP + ("CLINIC_NOTIFY_EMAIL",)),
    # 18h: a agenda do dia seguinte. Depois das 20h já não serve ao médico.
    Job("doctor_daily_agenda", "send_doctor_daily_agenda", At(18, grace_hours=2),
        _SUPABASE + _SMTP),
    Job("update_patient_ages", "update_patient_ages", At(0, monthday=1, grace_hours=12),
        _SUPABASE),
)


def missing_env(job: Job, environ=None) -> list[str]:
    environ = os.environ if environ is None else environ
    return [k for k in job.env if not environ.get(k)]


def is_due(job: Job, now: datetime, last_slot: datetime | None) -> datetime | None:
    """O slot a rodar agora, ou None. Vence quando o slot atual ainda não rodou e
    está dentro da janela de tolerância."""
    slot = job.schedule.slot(now)
    if last_slot is not None and slot <= last_slot:
        return None
    if now.astimezone(TZ) - slot > job.schedule.grace:
        return None
    return slot


class Scheduler:
    def __init__(self, jobs=JOBS, *, live: bool = SCHEDULER_LIVE, runner=None,
                 claim=None, log_event=None):
        self.jobs = jobs
        self.live = live
        self._runner = runner or run_job_subprocess
        self._claim = claim or claim_slot
        self._log_event = log_event or _log_event
        self.last_slot: dict[str, datetime] = {}
        self._queue: deque[tuple[Job, datetime]] = deque()
        self._pending: set[str] = set()  # na fila ou rodando
        self._wake = asyncio.Event()
        self._stopping = False
        self._tasks: list[asyncio.Task] = []

    async def boot(self) -> None:
        missing = {j.name: m for j in self.jobs if (m := missing_env(j))}
        if missing:
            logger.warning("SCHEDULER missing env: %s", missing)
        await self._log_event(EV_BOOT, {"live": self.live, "missing_env": missing})

    async def tick(self, now: datetime) -> None:
        """Enfileira as rotinas que venceram."""
        for job in self.jobs:
            if job.name in self._pending:
                continue
            slot = is_due(job, now, self.last_slot.get(job.name))
            if slot is None:
                continue
            if job.schedule.persisted and self.live:
                claimed = await self._claim(job, slot)
                if claimed is None:
                    continue  # banco não respondeu: tenta no próximo tick
                if not claimed:
                    self.last_slot[job.name] = slot  # já rodou antes do restart
                    continue
            self.last_slot[job.name] = slot
            self._pending.add(job.name)
            self._queue.append((job, slot))
        if self._queue:
            self._wake.set()

    async def _worker(self) -> None:
        while not self._stopping:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            job, slot = self._queue.popleft()
            try:
                if self.live:
                    await self._run_live(job, slot)
                else:
                    logger.info("SCHEDULER shadow would run %s slot=%s", job.name, slot.isoformat())
                    await self._log_event(EV_SHADOW, {"job": job.name, "slot": slot.isoformat()})
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("SCHEDULER job %s crashed", job.name)
            finally:
                self._pending.discard(job.name)

    async def _run_live(self, job: Job, slot: datetime) -> None:
        missing = missing_env(job)
        if missing:
            logger.error("SCHEDULER skip %s: missing env %s", job.name, missing)
            await self._log_event(EV_FAILED, {"job": job.name, "slot": slot.isoformat(),
                                              "error": f"missing env {missing}"})
            return
        started = time.monotonic()
        returncode, tail = await self._runner(job)
        elapsed = round(time.monotonic() - started, 1)
        if returncode == 0:
            logger.info("SCHEDULER %s ok in %ss", job.name, elapsed)
        else:
            logger.error("SCHEDULER %s failed rc=%s in %ss", job.name, returncode, elapsed)
            await self._log_event(EV_FAILED, {"job": job.name, "slot": slot.isoformat(),
                                              "returncode": returncode, "tail": tail})

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self.tick(datetime.now(TZ))
            except Exception:
                logger.exception("SCHEDULER tick failed")
            await asyncio.sleep(TICK_SECONDS)

    async def start(self) -> None:
        try:
            await self.boot()
        except Exception:
            logger.exception("SCHEDULER boot event failed")
        self._tasks = [asyncio.create_task(self._loop()), asyncio.create_task(self._worker())]
        logger.info("SCHEDULER started live=%s jobs=%d", self.live, len(self.jobs))

    async def stop(self) -> None:
        """Para de agendar; dá uma folga para a rotina em andamento terminar (um
        lembrete morto no meio pode ter enviado sem gravar o sent_at)."""
        self._stopping = True
        self._wake.set()
        loop_task, worker_task = self._tasks or (None, None)
        if loop_task:
            loop_task.cancel()
        if worker_task:
            try:
                await asyncio.wait_for(asyncio.shield(worker_task), SHUTDOWN_GRACE_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker_task.cancel()
            except Exception:
                pass


async def run_job_subprocess(job: Job) -> tuple[int, str]:
    """Roda `python -m scripts.<module>` e repassa a saída para o log da Eva.
    Devolve (returncode, últimas linhas)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", f"scripts.{job.module}",
        cwd=str(PROJECT_ROOT), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    tail: deque[str] = deque(maxlen=40)

    async def _pump():
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            tail.append(line)
            logger.info("[cron:%s] %s", job.name, line)

    try:
        await asyncio.wait_for(asyncio.gather(_pump(), proc.wait()), JOB_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        tail.append(f"timeout após {JOB_TIMEOUT_SECONDS}s")
        return -9, "\n".join(tail)[-1500:]
    except asyncio.CancelledError:
        # Shutdown passou da folga: encerra o filho junto.
        if proc.returncode is None:
            proc.terminate()
        raise
    return proc.returncode, "\n".join(tail)[-1500:]


async def claim_slot(job: Job, slot: datetime) -> bool | None:
    """Registra a rodada de horário fixo antes de rodar. False se já existe (rodou
    antes do restart). None se o banco não respondeu: não roda (fail closed) e o
    próximo tick tenta de novo, dentro da janela de tolerância."""
    from app.supabase_client import get_supabase
    slot_iso = slot.isoformat()
    try:
        client = await get_supabase()
        existing = (
            await client.from_("events").select("id")
            .eq("event_type", EV_RUN).eq("phone", EVENT_PHONE)
            .eq("metadata->>job", job.name).eq("metadata->>slot", slot_iso)
            .limit(1).execute()
        ).data
        if existing:
            return False
        await client.from_("events").insert({
            "event_type": EV_RUN, "phone": EVENT_PHONE,
            "metadata": {"job": job.name, "slot": slot_iso},
        }).execute()
        return True
    except Exception:
        logger.exception("SCHEDULER claim failed for %s %s", job.name, slot_iso)
        return None


async def _log_event(event_type: str, metadata: dict) -> None:
    from app.database import log_event
    await log_event(event_type, EVENT_PHONE, metadata)


_scheduler: Scheduler | None = None


async def start_scheduler() -> None:
    global _scheduler
    if os.getenv("SCHEDULER_ENABLED") != "1":
        logger.info("SCHEDULER disabled (SCHEDULER_ENABLED != 1)")
        return
    _scheduler = Scheduler()
    await _scheduler.start()


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
