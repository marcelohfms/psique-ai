"""Trava a regressão de import circular entre app.patients e app.database.

Em 2026-08-12, scripts/send_payment_reminders.py passou a importar app.patients
antes de app.database e o cron quebrou em 100% das execuções por ~2 dias. A
suíte normal não pegou: o conftest e os outros testes já carregaram
app.database primeiro, então a ordem que quebra nunca acontece dentro de um
processo de teste. Por isso cada caso aqui roda num subprocesso com sys.modules
limpo.

Os entrypoints testados não são uma lista mantida à mão — ela já driftou duas
vezes (scripts/_probe_chatwoot_number.py ficou fora, depois mais 4 scripts:
block_calendar_slots, send_doctor_daily_agenda, send_pending_payments_reminder,
update_patient_ages) sem que nada acusasse a lacuna. Em vez disso, os
entrypoints são derivados varrendo .github/workflows/*.yml por referências a
scripts/<nome>.py — a mesma fonte que decide o que roda em produção.
"""
import ast
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

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


def _discover_workflow_entrypoints() -> list[str]:
    """Deriva os módulos de entrypoint a partir de .github/workflows/*.yml.

    Regex simples sobre o texto do arquivo (sem parser YAML): qualquer
    referência a `scripts/<nome>.py` vira `scripts.<nome>`. Pega tanto os
    workflows agendados (cron) quanto os de workflow_dispatch (ex.:
    probe_chatwoot.yml) — qualquer script que um workflow do repo possa
    disparar em produção precisa importar limpo.
    """
    names = set()
    pattern = re.compile(r"scripts/([A-Za-z0-9_]+)\.py")
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        names.update(pattern.findall(path.read_text()))
    return sorted(f"scripts.{name}" for name in names)


WORKFLOW_ENTRYPOINTS = _discover_workflow_entrypoints()

# Uma varredura vazia (regex quebrado, diretório errado, workflows movidos)
# faria os parametrize abaixo passar silenciosamente com zero casos — o pior
# modo de falha possível para um teste de regressão. Falha alto e cedo em vez
# disso. 11 é a contagem real hoje (2026-08-14); o "no mínimo" deixa a
# asserção correta conforme mais workflows forem adicionados.
assert len(WORKFLOW_ENTRYPOINTS) >= 11, (
    f"esperava achar >= 11 entrypoints varrendo {WORKFLOWS_DIR}, achei "
    f"{len(WORKFLOW_ENTRYPOINTS)}: {WORKFLOW_ENTRYPOINTS}"
)

# app.main não aparece na varredura acima: é o serviço FastAPI, subido por
# deploy.yml via API do Easypanel (curl para services.app.deployService), que
# não cita scripts/<nome>.py — a varredura não tem como enxergá-lo. Ainda
# assim é o entrypoint de produção principal, e esta branch acrescentou uma
# aresta nova nele (app/main.py importa app.patients no topo), então precisa
# da mesma verificação de import limpo que os crons. Fica de fora da lista de
# guarda `__main__` usada pelo teste seguinte: é um módulo de aplicação
# (subido via uvicorn), não um script de cron — exigir a guarda dele seria um
# requisito errado.
CLEAN_IMPORT_ENTRYPOINTS = WORKFLOW_ENTRYPOINTS + ["app.main"]


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


@pytest.mark.parametrize("module", WORKFLOW_ENTRYPOINTS)
def test_cron_entrypoint_tem_guarda_main(module):
    """Sinaliza scripts de workflow sem guarda `if __name__ == "__main__":`.

    O teste `test_cron_entrypoint_importa_em_processo_limpo` importa cada
    entrypoint num subprocesso para verificar a cadeia de imports; isso só é
    seguro contra banco/API de produção se o script guardar a execução atrás
    dessa guarda — sem ela, `import` executaria o script de verdade. Este
    teste aqui NÃO é um gate para o outro: pytest não garante ordem de
    execução entre testes, e o CI roda `uv run pytest --tb=short` sem `-x`
    (.github/workflows/test.yml), então os dois rodam de forma independente.
    O papel deste teste é, por conta própria, apontar quando algum script
    novo entra em .github/workflows/ sem a guarda — não impedir que o outro
    teste rode.
    """
    rel_path = Path(*module.split(".")).with_suffix(".py")
    source = (REPO_ROOT / rel_path).read_text()
    assert re.search(r"""if __name__ == ['"]__main__['"]\s*:""", source), (
        f"{module} não tem guarda if __name__ == '__main__': importar o "
        f"módulo executaria o script"
    )


@pytest.mark.parametrize("module", CLEAN_IMPORT_ENTRYPOINTS)
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


@pytest.mark.parametrize("module", WORKFLOW_ENTRYPOINTS)
def test_entrypoint_resolve_imports_dentro_de_funcao(module):
    """Todo `from app.X import Y` de um entrypoint resolve — inclusive os lazy.

    `test_cron_entrypoint_importa_em_processo_limpo` só exercita os imports de
    **nível de módulo**: a guarda `if __name__ == "__main__"` impede main() de
    rodar, então um `from app.X import Y` dentro de um corpo de função nunca é
    avaliado e um símbolo inexistente passa despercebido até alguém disparar o
    script — tipicamente durante um incidente, que é quando essas ferramentas
    de diagnóstico são procuradas.

    Foi exatamente esse o caso de scripts/_probe_chatwoot_number.py, que
    importava `_find_conversation_for_contact` dentro de main() por meses
    depois de a função ter sido dividida em `_get_contact_conversations` +
    `_pick_conversation` (commit ca103a5).

    A verificação é estática: parseia o fonte e confere cada nome contra o
    módulo de origem, sem executar o entrypoint. Só resolve alvos `app.*` —
    importar um módulo `scripts.*` executaria aquele script.
    """
    rel_path = Path(*module.split(".")).with_suffix(".py")
    tree = ast.parse((REPO_ROOT / rel_path).read_text())

    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        # level > 0 é import relativo; module None acontece em `from . import x`
        if node.level or not node.module or not node.module.startswith("app."):
            continue
        origin = importlib.import_module(node.module)
        for alias in node.names:
            if not hasattr(origin, alias.name):
                missing.append(
                    f"linha {node.lineno}: {node.module} não tem {alias.name!r}"
                )

    assert not missing, (
        f"{module} importa símbolos que não existem:\n  " + "\n  ".join(missing)
    )
