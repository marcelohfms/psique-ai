"""Trava a regressão de import circular entre app.patients e app.database.

Em 2026-08-12, scripts/send_payment_reminders.py passou a importar app.patients
antes de app.database e o cron quebrou em 100% das execuções por ~2 dias. A
suíte normal não pegou: o conftest e os outros testes já carregaram
app.database primeiro, então a ordem que quebra nunca acontece dentro de um
processo de teste. Por isso cada caso aqui roda num subprocesso com sys.modules
limpo.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Importar um módulo não pode depender de credencial real. Se depender, é bug
# de import-time — o valor aqui é stub de propósito.
FAKE_ENV = {
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-key",
    "SUPABASE_CONNECTION_STRING": "",
    "OPENAI_API_KEY": "sk-test",
    "GOOGLE_CLIENT_ID": "test-client-id",
    "GOOGLE_CLIENT_SECRET": "test-secret",
    "GOOGLE_REFRESH_TOKEN": "test-refresh-token",
    "WHATSAPP_TOKEN": "test-token",
    "WHATSAPP_PHONE_NUMBER_ID": "123456789",
    "WHATSAPP_VERIFY_TOKEN": "test-verify-token",
    "META_APP_SECRET": "test-app-secret",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "CLINIC_NOTIFY_EMAIL": "",
}

# Todo script disparado por workflow agendado em .github/workflows/.
CRON_ENTRYPOINTS = [
    "scripts.send_payment_reminders",
    "scripts.send_appointment_reminders",
    "scripts.complete_appointments",
    "scripts.send_no_show_messages",
    "scripts.send_return_reminders",
    "scripts.release_pending_reschedules",
    "scripts._probe_chatwoot_number",
]


def _import_in_clean_subprocess(module: str) -> subprocess.CompletedProcess:
    """Importa `module` num interpretador novo, com sys.modules vazio.

    Os crons têm guarda `if __name__ == "__main__"`, então importar não executa
    main() — só dispara a cadeia de imports, que é o que queremos verificar.
    """
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=REPO_ROOT,
        env={**os.environ, **FAKE_ENV},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("module", CRON_ENTRYPOINTS)
def test_cron_entrypoint_importa_em_processo_limpo(module):
    result = _import_in_clean_subprocess(module)
    assert result.returncode == 0, (
        f"{module} não importa em processo limpo:\n{result.stderr}"
    )


def test_patients_importa_sem_database_carregado():
    """A ordem exata que derrubou a produção em 2026-08-12."""
    result = _import_in_clean_subprocess("app.patients")
    assert result.returncode == 0, (
        f"app.patients não importa primeiro:\n{result.stderr}"
    )


def test_database_importa_sem_patients_carregado():
    """A ordem que já funcionava — não pode regredir."""
    result = _import_in_clean_subprocess("app.database")
    assert result.returncode == 0, (
        f"app.database não importa primeiro:\n{result.stderr}"
    )


def test_patients_nao_importa_database():
    """Trava a aresta de volta contra reintrodução acidental.

    app/patients.py deve importar apenas dos módulos-folha (app.phone,
    app.supabase_client). Se alguém reintroduzir um import de app.database
    aqui, o ciclo renasce e o próximo script que importar patients primeiro
    quebra de novo — silenciosamente, até um cron falhar em produção.
    """
    source = (REPO_ROOT / "app" / "patients.py").read_text()

    assert "from app.database import" not in source
    assert "import app.database" not in source


def test_ninguem_importa_phone_variants_de_database():
    """_phone_variants pertence a app.phone; app.database não o usa.

    Manter o re-export significaria dois caminhos de import para o mesmo
    símbolo — a confusão que produziu o ciclo patients/database.
    """
    offenders = []
    for path in REPO_ROOT.rglob("*.py"):
        if ".venv" in path.parts or path.name == "test_import_graph.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if "from app.database import" in line and "_phone_variants" in line:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

    assert not offenders, (
        "importe _phone_variants de app.phone:\n  " + "\n  ".join(offenders)
    )
