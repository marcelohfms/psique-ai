# Contatos não-paciente — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer o bot reconhecer contatos não-paciente (representantes) via `manual_hold` em contato solto, e reconciliar os 8 representantes conhecidos para contatos soltos com `manual_hold=true`.

**Architecture:** O silêncio por label `eva-inativa` JÁ EXISTE no webhook (`app/main.py`) e não é tocado. Falta (1) fazer `process_message` honrar o `manual_hold` lido direto do contato (o check atual só olha pacientes, perdendo contato solto), e (2) um script único que transforma os representantes em contatos soltos com `manual_hold=true`.

**Tech Stack:** Python 3 + Supabase async + FastAPI, pytest com Supabase mockado (`tests/conftest.py`).

**Spec:** `docs/superpowers/specs/2026-06-23-contatos-nao-paciente-design.md`

---

## File Structure

- **Modify:** `app/main.py` — em `process_message`, honrar `manual_hold` do contato (via `get_contact_by_phone`).
- **Create:** `scripts/reconcile_representantes.py` — script único de reconciliação (8 reps → contatos soltos com `manual_hold=true`).
- **Modify:** `tests/test_process_message.py` — teste do silêncio por `manual_hold` em contato solto.

---

## Task 1: `process_message` honra `manual_hold` do contato solto

**Files:**
- Modify: `app/main.py` (imports + `process_message`, perto do check `manual_hold` existente ~linha 329)
- Test: `tests/test_process_message.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicione a `tests/test_process_message.py` (usa os mesmos padrões de `test_new_user_initializes_collect_info`):

```python
async def test_standalone_contact_with_manual_hold_is_silenced():
    """Contato solto (sem paciente) com manual_hold=true → bot não processa."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
             patch("app.main.get_contact_by_phone", new_callable=AsyncMock,
                   return_value={"id": "c-rep", "phone": "5581997556159", "manual_hold": True}), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi, sou representante")
            chatbot.ainvoke.assert_not_called()   # silêncio: nada foi processado
    finally:
        gg.chatbot = original


async def test_contact_without_manual_hold_proceeds_normally():
    """Contato solto SEM manual_hold → fluxo normal (regressão)."""
    import app.graph.graph as gg
    chatbot = _make_chatbot()
    original = gg.chatbot
    gg.chatbot = chatbot
    try:
        with patch("app.main.get_user_by_phone", new_callable=AsyncMock, return_value=None), \
             patch("app.main.get_users_by_phone", new_callable=AsyncMock, return_value=[]), \
             patch("app.main.get_contact_by_phone", new_callable=AsyncMock,
                   return_value={"id": "c1", "phone": "5583999999999", "manual_hold": False}), \
             patch("app.main.log_event", new_callable=AsyncMock):
            from app.main import process_message
            await process_message(PHONE, "oi")
            chatbot.ainvoke.assert_called()   # seguiu o fluxo
    finally:
        gg.chatbot = original
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_process_message.py -k "manual_hold or without_manual_hold" -v`
Expected: `test_standalone_contact_with_manual_hold_is_silenced` FALHA — hoje `process_message` não consulta `get_contact_by_phone`, então `chatbot.ainvoke` É chamado. (`AttributeError`/`assert_not_called` falha.)
Nota: se `app.main` ainda não tiver `get_contact_by_phone` importado, o `patch("app.main.get_contact_by_phone", ...)` dá `AttributeError` — esperado até o Step 3.

- [ ] **Step 3: Implementar**

Em `app/main.py`, adicione o import (junto dos imports de `app.database` existentes, ex.: onde `get_user_by_phone` é importado):

```python
from app.database import get_contact_by_phone
```

Em `process_message`, logo após o check de `manual_hold` via pacientes existente:

```python
    if any(r.get("manual_hold") for r in all_users):
        return  # permanent hold — never reactivates
```

adicione o check via contato (cobre contato solto, sem paciente):

```python
    # Contato solto (não-paciente, ex.: representante) também é silenciado.
    # O check acima itera sobre pacientes; um contato sem paciente não apareceria.
    try:
        _contact = await get_contact_by_phone(phone)
    except Exception:
        _contact = None  # degradação graciosa — segue o fluxo normal
    if _contact and _contact.get("manual_hold"):
        return
```

- [ ] **Step 4: Rodar e ver passar**

Run: `uv run pytest tests/test_process_message.py -k "manual_hold or without_manual_hold" -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Suíte completa**

Run: `uv run pytest --tb=short`
Expected: PASS (todos). Conserte qualquer regressão.

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_process_message.py
git commit -m "feat: process_message silencia contato solto com manual_hold (não-paciente)"
```

---

## Task 2: Script de reconciliação dos representantes

**Files:**
- Create: `scripts/reconcile_representantes.py`

- [ ] **Step 1: Escrever o script**

```python
"""Reconcilia os representantes (não-pacientes) para contatos soltos.

Para cada representante:
  - Se há um paciente no banco com esse contato: apaga o registro de `patients`
    + os vínculos `patient_contacts`, MANTÉM o `contact`, e seta manual_hold=true.
    Guarda: só apaga o paciente se o contato não servir a outro paciente real.
  - Se não há contato: cria um contact solto com manual_hold=true.

Uso:
    uv run python scripts/reconcile_representantes.py [--exec]
(sem --exec = dry-run, só imprime)
"""
import asyncio
import sys

from dotenv import load_dotenv
load_dotenv()

import app.database  # noqa: F401 — carrega database antes de patients (evita import circular)
from app.database import get_supabase
from app.patients import normalize_phone

# nome (referência) -> número informado
REPS = {
    "Tássio Medeiros": "5581997556159",
    "Raísa Lima": "5581986215099",
    "João Alexandre": "5581996590590",
    "Emerson": "5581999940120",
    "Luísa Almeida": "5581981579151",
    "Julio Barbosa": "5581997358795",
    "Joanna Lira": "5581986038837",
    "Morgana Araújo": "5581995293210",
}


async def main(dry_run: bool) -> None:
    client = await get_supabase()
    for nome, raw in REPS.items():
        phone = normalize_phone(raw)
        existing = (await client.from_("contacts").select("id").eq("phone", phone).execute()).data or []
        if existing:
            cid = existing[0]["id"]
            # pacientes ligados a este contato
            links = (await client.from_("patient_contacts").select("patient_id").eq("contact_id", cid).execute()).data or []
            pids = {l["patient_id"] for l in links}
            print(f"{nome} ({phone}): contato {cid[-6:]} existe; {len(pids)} paciente(s) vinculado(s)")
            if not dry_run:
                for pid in pids:
                    # só apaga o paciente se ele não tiver OUTRO contato (é o registro do rep)
                    other = (await client.from_("patient_contacts").select("contact_id").eq("patient_id", pid).execute()).data or []
                    distinct = {o["contact_id"] for o in other}
                    if distinct == {cid}:
                        await client.from_("patients").delete().eq("id", pid).execute()
                        print(f"   paciente {pid[-6:]} apagado")
                    else:
                        # contato compartilhado com paciente real — só desvincula este contato
                        await client.from_("patient_contacts").delete().eq("patient_id", pid).eq("contact_id", cid).execute()
                        print(f"   paciente {pid[-6:]} mantido (contato compartilhado) — vínculo removido")
                # remove vínculos remanescentes deste contato e marca manual_hold
                await client.from_("patient_contacts").delete().eq("contact_id", cid).execute()
                await client.from_("contacts").update({"manual_hold": True}).eq("id", cid).execute()
                print(f"   contato {cid[-6:]} agora solto com manual_hold=true")
        else:
            print(f"{nome} ({phone}): sem contato — criar solto com manual_hold=true")
            if not dry_run:
                await client.from_("contacts").insert({"phone": phone, "name": nome, "manual_hold": True}).execute()
                print(f"   contato criado")
    print(f"\n{'DRY-RUN (nada gravado)' if dry_run else 'RECONCILIAÇÃO CONCLUÍDA'}")


if __name__ == "__main__":
    asyncio.run(main(dry_run="--exec" not in sys.argv))
```

- [ ] **Step 2: Verificar sintaxe/imports (NÃO conecta ao banco)**

Run:
```bash
uv run python -c "import importlib.util; s=importlib.util.spec_from_file_location('m','scripts/reconcile_representantes.py'); mod=importlib.util.module_from_spec(s); s.loader.exec_module(mod); print('ok', bool(mod.main))"
```
Expected: `ok True`

- [ ] **Step 3: Commit**

```bash
git add scripts/reconcile_representantes.py
git commit -m "feat: script de reconciliação de representantes (contatos soltos com manual_hold)"
```

- [ ] **Step 4: Executar (passo manual do usuário, fora do plano de código)**

NÃO executar automaticamente. O usuário roda:
```bash
uv run python scripts/reconcile_representantes.py          # dry-run, revisa
uv run python scripts/reconcile_representantes.py --exec   # aplica
```
Revisar o dry-run antes do `--exec`.

---

## Verificação final

- [ ] `uv run pytest --tb=short` — toda a suíte passa
- [ ] Dry-run do script mostra: 5 reps com paciente a remover, 3 sem contato a criar
- [ ] Após `--exec`: os 8 viram contatos soltos com `manual_hold=true`; `process_message` os silencia
