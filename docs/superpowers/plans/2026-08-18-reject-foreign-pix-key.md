# Barrar comprovante PIX para chave que não é da clínica — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Impedir que `register_payment` grave um pagamento quando o comprovante mostra, de forma inequívoca, uma chave de destino diferente do CNPJ da clínica (`42006848000178`); nesses casos a Eva avisa o paciente para refazer o PIX na chave certa.

**Architecture:** Uma função pura `_receipt_destination_is_foreign(image_description)` em `app/graph/tools.py` decide se o comprovante é claramente para outra chave (fail-open em ambiguidade). Um guard no topo de `register_payment` chama essa função e retorna uma mensagem de aviso antes de qualquer efeito colateral (Supabase, Drive, planilha). Reforço secundário no system prompt.

**Tech Stack:** Python 3.14, LangGraph tools (`@tool`), pytest, uv.

**Worktree:** `.worktrees/reject-foreign-pix-key` (já criada). Rode tudo de dentro dela.

**Spec:** `docs/superpowers/specs/2026-08-18-reject-foreign-pix-key-design.md`

---

## File Structure

- **Modify** `app/graph/tools.py`
  - Novo import top-level: `from app.graph.prompts import CORRECT_PIX_KEY`.
  - Nova função pura de módulo `_receipt_destination_is_foreign(image_description: str) -> bool`.
  - Guard no início de `register_payment` (entre a linha `from app.google_sheets import append_payment_receipt` e `phone = config["configurable"]["phone"]`).
- **Modify** `app/graph/prompts.py`
  - Uma linha no bloco de regras de pagamento reforçando a conferência do destinatário.
- **Modify** `tests/test_tools.py`
  - Testes unitários da função pura + 1 teste de integração do guard (early-return sem efeitos colaterais).

---

## Task 1: Função pura `_receipt_destination_is_foreign`

**Files:**
- Modify: `app/graph/tools.py` (novo import + nova função de módulo)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

Adicione ao final de `tests/test_tools.py`:

```python
# ── Guard: comprovante PIX para chave que não é da clínica ───────────────────
from app.graph.tools import _receipt_destination_is_foreign


def test_foreign_phone_key_is_flagged():
    # Caso real João Pedro: PIX para a própria chave-telefone, não para o CNPJ.
    desc = ("COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, "
            "chave PIX +55 81 99242 4522, nome do destinatário José Reinaldo da Costa "
            "Gomes Filho, data/hora da transação 18/08/2026 - 11:00:07.")
    assert _receipt_destination_is_foreign(desc) is True


def test_clinic_cnpj_with_punctuation_passes():
    desc = ("COMPROVANTE DE PAGAMENTO: valor R$ 100,00, "
            "chave PIX 42.006.848/0001-78, nome do destinatário PSIQUE, 18 AGO 2026.")
    assert _receipt_destination_is_foreign(desc) is False


def test_clinic_cnpj_plain_digits_passes():
    desc = ("COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX 42006848000178, "
            "destinatário PSIQUE.")
    assert _receipt_destination_is_foreign(desc) is False


def test_masked_key_without_foreign_key_passes():
    # Máscara curta, sem chave estrangeira legível → fail-open.
    desc = "COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX ***.848/1-78, PSIQUE."
    assert _receipt_destination_is_foreign(desc) is False


def test_empty_description_passes():
    assert _receipt_destination_is_foreign("") is False


def test_third_party_cpf_is_flagged():
    desc = ("COMPROVANTE DE PAGAMENTO: R$ 100,00, chave PIX 123.456.789-00, "
            "nome do destinatário Fulano de Tal, 18/08/2026.")
    assert _receipt_destination_is_foreign(desc) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py -k "foreign or clinic_cnpj or masked_key or empty_description or third_party_cpf" -v`
Expected: FAIL com `ImportError: cannot import name '_receipt_destination_is_foreign'`.

- [ ] **Step 3: Add the module-level import**

Em `app/graph/tools.py`, junto aos imports top-level (após a linha 17, `from app.chatwoot import ...`), adicione:

```python
from app.graph.prompts import CORRECT_PIX_KEY
```

- [ ] **Step 4: Implement the pure function**

Em `app/graph/tools.py`, adicione a função de módulo logo antes de `@tool`/`async def register_payment` (por volta da linha 2923). Use o `re` já importado no topo do módulo (linha 3):

```python
_CLINIC_PIX_DIGITS = re.sub(r"\D", "", CORRECT_PIX_KEY)  # "42006848000178"


def _receipt_destination_is_foreign(image_description: str) -> bool:
    """True somente quando o comprovante mostra INEQUIVOCAMENTE uma chave de
    destino diferente da chave PIX da clínica (CORRECT_PIX_KEY).

    Fail-open: retorna False em qualquer caso ambíguo — texto vazio, sem token de
    chave legível, ou chave mascarada/curta (< 11 dígitos). Só barra quando há um
    token de chave/CPF/CNPJ com >= 11 dígitos que não casa (nem por substring) com
    o CNPJ da clínica.
    """
    if not image_description:
        return False

    full_digits = re.sub(r"\D", "", image_description)
    # CNPJ da clínica aparece em qualquer lugar do texto → é da clínica.
    if _CLINIC_PIX_DIGITS in full_digits:
        return False

    # Extrai o token de destino: trecho após "chave PIX" / "CPF" / "CNPJ" até
    # a próxima vírgula, ponto-e-vírgula ou fim de linha.
    m = re.search(
        r"(?:chave\s*pix|cpf\s*/?\s*cnpj|cnpj|cpf)\s*[:\-]?\s*([^,;\n]+)",
        image_description,
        re.IGNORECASE,
    )
    if not m:
        return False

    dest_digits = re.sub(r"\D", "", m.group(1))
    if len(dest_digits) < 11:
        return False  # mascarada/curta → fail-open

    if _CLINIC_PIX_DIGITS in dest_digits or dest_digits in _CLINIC_PIX_DIGITS:
        return False  # casa (inclusive máscara que é substring) → clínica

    return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -k "foreign or clinic_cnpj or masked_key or empty_description or third_party_cpf" -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(payment): detecta comprovante PIX para chave que não é da clínica

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Guard em `register_payment` (early-return sem efeitos colaterais)

**Files:**
- Modify: `app/graph/tools.py:2949-2951` (topo do corpo de `register_payment`)
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing integration test**

Adicione ao final de `tests/test_tools.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.graph.tools import register_payment


@pytest.mark.asyncio
async def test_register_payment_blocks_foreign_key_no_side_effects():
    desc = ("COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, "
            "chave PIX +55 81 99242 4522, nome do destinatário José Reinaldo, "
            "18/08/2026 - 11:00:07.")
    state = {"messages": [], "preferred_doctor": "julio"}
    config = {"configurable": {"phone": "5581992424522"}}

    with patch("app.graph.tools.get_supabase", new_callable=AsyncMock) as mock_db, \
         patch("app.google_sheets.append_payment_receipt", new_callable=AsyncMock) as mock_sheet:
        result = await register_payment.coroutine(
            amount="100,00",
            drive_link="https://drive.google.com/file/d/ABC/view",
            state=state,
            config=config,
            image_description=desc,
        )

    assert "42006848000178" in result
    assert "outra chave" in result.lower()
    mock_db.assert_not_called()       # rejeitou antes de tocar o Supabase
    mock_sheet.assert_not_called()    # nada gravado na planilha
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_register_payment_blocks_foreign_key_no_side_effects -v`
Expected: FAIL — hoje `register_payment` chama `get_supabase` (mock_db é chamado) e não retorna o aviso.

- [ ] **Step 3: Insert the guard**

Em `app/graph/tools.py`, dentro de `register_payment`, entre a linha `from app.google_sheets import append_payment_receipt` (~2949) e `phone = config["configurable"]["phone"]` (~2951), insira:

```python
    # ── Guard: comprovante para chave que não é da clínica ─────────────────────
    # Só inspeciona quando há imagem de comprovante; pagamentos do painel/atendente
    # (is_link / payment_method, sem image_description) passam direto.
    if image_description and not is_link and not payment_method:
        if _receipt_destination_is_foreign(image_description):
            _logger.warning(
                "REGISTER_PAYMENT blocked: destino estrangeiro | desc=%r",
                image_description[:160],
            )
            return (
                "⚠️ Esse comprovante foi para outra chave PIX, não para a da clínica. "
                f"NÃO registrei o pagamento. Peça ao paciente para conferir e refazer o "
                f"PIX para a chave {CORRECT_PIX_KEY} (CNPJ PSIQUE) e reenviar o comprovante."
            )
```

Nota: `_logger` já está definido acima (linha ~2947). O guard vem **antes** de `client = await get_supabase()`, garantindo o early-return sem efeitos colaterais.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_tools.py::test_register_payment_blocks_foreign_key_no_side_effects -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/graph/tools.py tests/test_tools.py
git commit -m "feat(payment): register_payment barra comprovante para chave errada antes de gravar

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Reforço no system prompt

**Files:**
- Modify: `app/graph/prompts.py`

- [ ] **Step 1: Localize the payment rules block**

Run: `grep -n "taxa de reserva\|comprovante\|register_payment\|CORRECT_PIX_KEY" app/graph/prompts.py | head`
Escolha o bloco de instruções onde a Eva é orientada a registrar comprovantes (perto de onde `CORRECT_PIX_KEY` já é interpolado / onde se fala em registrar o comprovante).

- [ ] **Step 2: Add the reinforcement line**

Adicione uma linha objetiva nesse bloco (ajuste a redação ao estilo do bloco vizinho):

```
- Antes de registrar um comprovante, confira o DESTINATÁRIO: só registre se o PIX foi para a chave da clínica ({key}). Se o comprovante mostrar outra chave/CPF/CNPJ, NÃO registre — avise o paciente para refazer o PIX na chave correta e reenviar o comprovante.
```

Se o bloco usar `.format(key=...)` / f-string com `CORRECT_PIX_KEY`, interpole a chave da mesma forma que as linhas vizinhas; senão escreva a chave literal `42006848000178`, seguindo o padrão local do arquivo.

- [ ] **Step 3: Verify prompt still builds**

Run: `uv run python -c "import app.graph.prompts as p; print('prompts OK')"`
Expected: `prompts OK` (sem erro de `.format`/f-string).

- [ ] **Step 4: Commit**

```bash
git add app/graph/prompts.py
git commit -m "feat(payment): prompt reforça conferência do destinatário antes de registrar comprovante

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Suíte completa verde

**Files:** nenhum (verificação)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest --tb=short`
Expected: todos passam (ver memória sobre ordem de arquivos: não passe `test_patients.py` como primeiro argumento — aqui rodamos a suíte inteira sem argumento, então tudo bem).

- [ ] **Step 2: If green, no commit needed**

Se algum teste pré-existente quebrar por causa da mudança, corrija-o no espírito do novo comportamento (o guard só afeta comprovantes com imagem e chave claramente estrangeira) e commite com mensagem `test: ajusta <arquivo> ao guard de chave PIX`.

---

## Self-Review

**Spec coverage:**
- Função pura fail-open + só-CNPJ → Task 1 ✅
- Guard early-return sem efeitos colaterais, só com imagem → Task 2 ✅
- Só avisa paciente (mensagem de retorno, sem evento/e-mail) → Task 2 ✅
- Painel/atendente não afetado (`is_link`/`payment_method`) → guard condicional em Task 2 + coberto pela lógica (sem `image_description` não entra) ✅
- Reforço no prompt → Task 3 ✅
- Testes: função pura (6 casos) + integração (1) → Tasks 1–2 ✅
- Fora de escopo (evento p/ clínica, casar por nome, reprocessar retroativo) → não incluído ✅

**Placeholder scan:** sem TBD/TODO; todo passo tem código/comando concretos. Task 3 Step 2 deixa a redação exata dependente do bloco vizinho por design (segue o estilo do arquivo), mas fornece a linha pronta.

**Type consistency:** `_receipt_destination_is_foreign(str) -> bool` e `_CLINIC_PIX_DIGITS` usados de forma idêntica em Tasks 1 e 2. `register_payment.coroutine(...)` casa com o estilo de chamada de tools nos testes existentes.
