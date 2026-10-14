"""Agendador interno das rotinas (app/scheduler.py)."""
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app import scheduler as sch
from app.scheduler import TZ, At, Every, Job, Scheduler, is_due, missing_env

ROOT = Path(__file__).resolve().parent.parent


def _t(day, hour, minute=0):
    # 13/10/2026 é uma terça-feira.
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# ── Every ─────────────────────────────────────────────────────────────────────

def test_every_slot_aligned_to_clock():
    assert Every(30).slot(_t(13, 10, 47)) == _t(13, 10, 30)
    assert Every(10).slot(_t(13, 10, 9)) == _t(13, 10, 0)
    assert Every(60).slot(_t(13, 10, 59)) == _t(13, 10, 0)


def test_every_slot_converts_utc_to_recife():
    from datetime import timezone
    utc = datetime(2026, 10, 13, 14, 12, tzinfo=timezone.utc)  # 11:12 Recife
    assert Every(30).slot(utc) == _t(13, 11, 0)


def test_every_runs_once_per_slot():
    job = Job("x", "x", Every(30))
    slot = is_due(job, _t(13, 10, 31), None)
    assert slot == _t(13, 10, 30)
    assert is_due(job, _t(13, 10, 50), slot) is None
    assert is_due(job, _t(13, 11, 0), slot) == _t(13, 11, 0)


# ── At ────────────────────────────────────────────────────────────────────────

def test_at_daily_before_and_after_hour():
    a = At(8)
    assert a.slot(_t(13, 7, 59)) == _t(12, 8)
    assert a.slot(_t(13, 8, 0)) == _t(13, 8)


def test_at_weekdays_skips_to_last_matching_day():
    monday = At(8, weekdays=(0,))
    assert monday.slot(_t(13, 9)) == _t(12, 8)  # terça → segunda anterior
    mon_sat = At(9, weekdays=(0, 1, 2, 3, 4, 5))
    assert mon_sat.slot(_t(18, 10)) == _t(17, 9)  # domingo → sábado


def test_at_monthday():
    assert At(0, monthday=1).slot(_t(13, 10)) == _t(1, 0)


def test_at_grace_window():
    job = Job("x", "x", At(8, grace_hours=4))
    assert is_due(job, _t(13, 11, 59), None) == _t(13, 8)
    assert is_due(job, _t(13, 12, 1), None) is None  # passou da janela


def test_doctor_agenda_not_sent_late_at_night():
    job = next(j for j in sch.JOBS if j.name == "doctor_daily_agenda")
    assert is_due(job, _t(13, 19, 30), None) == _t(13, 18)
    assert is_due(job, _t(13, 21, 0), None) is None


# ── Inventário ────────────────────────────────────────────────────────────────

def test_every_job_module_exists():
    for job in sch.JOBS:
        assert (ROOT / "scripts" / f"{job.module}.py").exists(), job.module


def test_jobs_cover_github_workflow_secrets():
    """Cada rotina declara as mesmas variáveis que o workflow do GitHub passava,
    para o aviso de variável faltando na Eva ser fiel."""
    wf_dir = ROOT / ".github" / "workflows"
    for job in sch.JOBS:
        wf = next(
            (p for p in wf_dir.glob("*.yml") if f"scripts/{job.module}.py" in p.read_text()),
            None,
        )
        if wf is None:
            continue  # workflow já removido
        secrets = set(re.findall(r"secrets\.([A-Z_]+)", wf.read_text()))
        assert secrets <= set(job.env), (job.name, secrets - set(job.env))


def test_missing_env():
    job = Job("x", "x", Every(10), ("A", "B"))
    assert missing_env(job, {"A": "1", "B": ""}) == ["B"]


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _scheduler(jobs, *, live=True, claim=None, runner=None):
    return Scheduler(
        jobs, live=live,
        runner=runner or AsyncMock(return_value=(0, "")),
        claim=claim or AsyncMock(return_value=True),
        log_event=AsyncMock(),
    )


async def _drain(s):
    while s._queue:
        job, slot = s._queue.popleft()
        try:
            await s._run_live(job, slot)
        finally:
            s._pending.discard(job.name)


async def test_tick_enqueues_due_job_once():
    job = Job("x", "x", Every(30))
    s = _scheduler((job,))
    await s.tick(_t(13, 10, 31))
    await s.tick(_t(13, 10, 32))  # ainda na fila: não duplica
    assert len(s._queue) == 1
    await _drain(s)
    await s.tick(_t(13, 10, 40))  # mesmo slot, já rodou
    assert not s._queue


async def test_persisted_job_skipped_when_already_claimed():
    job = Job("x", "x", At(8))
    s = _scheduler((job,), claim=AsyncMock(return_value=False))
    await s.tick(_t(13, 9))
    assert not s._queue
    assert s.last_slot["x"] == _t(13, 8)


async def test_persisted_job_retries_when_claim_read_fails():
    job = Job("x", "x", At(8))
    claim = AsyncMock(side_effect=[None, True])
    s = _scheduler((job,), claim=claim)
    await s.tick(_t(13, 9))
    assert not s._queue and "x" not in s.last_slot
    await s.tick(_t(13, 9, 1))
    assert len(s._queue) == 1


async def test_interval_job_does_not_touch_claims():
    claim = AsyncMock(return_value=True)
    s = _scheduler((Job("x", "x", Every(10)),), claim=claim)
    await s.tick(_t(13, 9))
    claim.assert_not_awaited()


async def test_shadow_mode_never_runs_or_claims():
    import asyncio
    claim = AsyncMock(return_value=True)
    runner = AsyncMock(return_value=(0, ""))
    s = _scheduler((Job("x", "x", At(8)),), live=False, claim=claim, runner=runner)
    await s.tick(_t(13, 9))
    task = asyncio.create_task(s._worker())
    for _ in range(5):
        await asyncio.sleep(0)
    s._stopping = True
    s._wake.set()
    await asyncio.wait_for(task, 1)
    claim.assert_not_awaited()
    runner.assert_not_awaited()
    s._log_event.assert_awaited_with(sch.EV_SHADOW, {"job": "x", "slot": _t(13, 8).isoformat()})


async def test_failed_job_logs_event():
    job = Job("x", "x", Every(10))
    s = _scheduler((job,), runner=AsyncMock(return_value=(1, "Traceback boom")))
    await s._run_live(job, _t(13, 9))
    ev_type, meta = s._log_event.await_args.args
    assert ev_type == sch.EV_FAILED and meta["returncode"] == 1 and "boom" in meta["tail"]


async def test_missing_env_skips_run(monkeypatch):
    monkeypatch.delenv("ZZ_NAO_EXISTE", raising=False)
    job = Job("x", "x", Every(10), ("ZZ_NAO_EXISTE",))
    runner = AsyncMock(return_value=(0, ""))
    s = _scheduler((job,), runner=runner)
    await s._run_live(job, _t(13, 9))
    runner.assert_not_awaited()
    assert s._log_event.await_args.args[0] == sch.EV_FAILED


async def test_boot_reports_missing_env(monkeypatch):
    monkeypatch.delenv("ZZ_NAO_EXISTE", raising=False)
    s = _scheduler((Job("x", "x", Every(10), ("ZZ_NAO_EXISTE",)),))
    await s.boot()
    s._log_event.assert_awaited_once_with(
        sch.EV_BOOT, {"live": True, "missing_env": {"x": ["ZZ_NAO_EXISTE"]}})


async def test_start_scheduler_requires_flag(monkeypatch):
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    started = AsyncMock()
    monkeypatch.setattr(Scheduler, "start", started)
    await sch.start_scheduler()
    started.assert_not_awaited()
    assert sch._scheduler is None


async def test_run_job_subprocess_real_process(monkeypatch, tmp_path):
    """Roda um módulo de verdade e captura saída e código de saída."""
    pkg = tmp_path / "scripts"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "_ok.py").write_text("print('ola')\n")
    (pkg / "_fail.py").write_text("raise SystemExit(3)\n")
    monkeypatch.setattr(sch, "PROJECT_ROOT", tmp_path)
    rc, tail = await sch.run_job_subprocess(Job("ok", "_ok", Every(10)))
    assert rc == 0 and "ola" in tail
    rc, _ = await sch.run_job_subprocess(Job("fail", "_fail", Every(10)))
    assert rc == 3


async def test_claim_slot_fails_closed_on_db_error(monkeypatch):
    async def _boom():
        raise RuntimeError("dns")
    monkeypatch.setattr("app.supabase_client.get_supabase", _boom)
    assert await sch.claim_slot(Job("x", "x", At(8)), _t(13, 8)) is None


# ── Guardas da virada (2026-10-14) ──────────────────────────────────────────────

def test_scheduler_is_live():
    """A virada: o agendador executa de verdade. Não reverter sem querer."""
    assert sch.SCHEDULER_LIVE is True


def test_no_job_requires_pix_key():
    """PIX_KEY é falso-positivo: os scripts usam a constante CORRECT_PIX_KEY.
    Se voltar ao env, o _run_live pula a cobrança de taxa por 'missing env'."""
    for job in sch.JOBS:
        assert "PIX_KEY" not in job.env, job.name
