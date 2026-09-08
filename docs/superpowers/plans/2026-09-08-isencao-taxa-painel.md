# Isenção permanente da taxa de reserva no painel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar à atendente um checkbox no painel para marcar um paciente como sempre isento da taxa de reserva, ligando a flag `patients.booking_fee_waived` que já existe no banco.

**Architecture:** A coluna `patients.booking_fee_waived` (BOOL) já existe e já é lida pelo bot no momento do agendamento (`app/graph/tools.py:1539-1561`), então não há migration nem rota nova. Basta expor o campo no formulário "Paciente" do painel e liberá-lo na whitelist de campos editáveis do backend.

**Tech Stack:** FastAPI + Jinja2, front vanilla JS em `dashboard/templates/atendente.html`, camada de dados `dashboard/attendant_db.py` (Supabase), pytest.

---

### Task 1: Liberar `booking_fee_waived` na whitelist do backend (TDD)

O `update_patient` filtra o payload por `_PATIENT_FIELDS` (`dashboard/attendant_db.py:161-165`). Sem `booking_fee_waived` nesse set, o `_filter` descarta o campo em silêncio e o salvamento não persiste. Este é o ponto que faz o checkbox funcionar de verdade.

**Files:**
- Modify: `dashboard/attendant_db.py:161-165`
- Test: `tests/test_dashboard_attendant_db.py`

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao fim de `tests/test_dashboard_attendant_db.py`:

```python
def test_booking_fee_waived_in_patient_fields_whitelist():
    """Permite à atendente ligar/desligar a isenção permanente da taxa de reserva.

    Sem estar na whitelist, o _filter de update_patient descartaria o campo
    em silêncio e o salvamento pelo painel não persistiria.
    """
    assert "booking_fee_waived" in _PATIENT_FIELDS
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `uv run pytest tests/test_dashboard_attendant_db.py::test_booking_fee_waived_in_patient_fields_whitelist -v`
Expected: FAIL (`assert 'booking_fee_waived' in _PATIENT_FIELDS`)

- [ ] **Step 3: Adicionar o campo à whitelist**

Em `dashboard/attendant_db.py`, o set `_PATIENT_FIELDS` passa a ser:

```python
_PATIENT_FIELDS = {
    "name", "birth_date", "age", "patient_cpf", "email", "doctor_id",
    "is_returning_patient", "modality_restriction", "age_exception", "custom_price",
    "financial_name", "financial_cpf", "financial_email", "social_name",
    "booking_fee_waived",
}
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `uv run pytest tests/test_dashboard_attendant_db.py -v`
Expected: PASS (todos os testes do arquivo)

- [ ] **Step 5: Commit**

```bash
git add dashboard/attendant_db.py tests/test_dashboard_attendant_db.py
git commit -m "feat(painel): libera booking_fee_waived na whitelist do paciente"
```

---

### Task 2: Checkbox "Taxa de reserva sempre isenta" no formulário

Adiciona o controle visual na aba "Paciente" e inclui o campo no payload enviado ao salvar. É front-end puro (JS inline no template), sem cobertura de teste unitário no projeto — a verificação é manual no painel (Task 3 cuida da checagem final).

**Files:**
- Modify: `dashboard/templates/atendente.html` (bloco de checkboxes da aba Paciente, ~linhas 360-361; função `savePatient`, ~linhas 404-410)

- [ ] **Step 1: Adicionar o checkbox no bloco da aba Paciente**

Localizar o bloco (por volta da linha 360):

```html
      <div class="flex flex-wrap gap-4 mt-1 mb-2">
        ${checkbox("Paciente retornante", "p_returning", patient.is_returning_patient)}
        ${checkbox("Exceção de idade", "p_age_exc", patient.age_exception)}
      </div>
```

Substituir por:

```html
      <div class="flex flex-wrap gap-4 mt-1 mb-2">
        ${checkbox("Paciente retornante", "p_returning", patient.is_returning_patient)}
        ${checkbox("Exceção de idade", "p_age_exc", patient.age_exception)}
        ${checkbox("Taxa de reserva sempre isenta", "p_fee_waived", patient.booking_fee_waived)}
      </div>
```

- [ ] **Step 2: Enviar o campo no payload de `savePatient`**

Localizar a função `savePatient` (por volta da linha 404):

```javascript
async function savePatient(pid) {
  const ok = await post(`/api/atendente/paciente/${pid}`, { data: {
    name: val("p_name"), social_name: val("p_social_name"), birth_date: val("p_birth"), patient_cpf: val("p_cpf"),
    email: val("p_email"), doctor_id: val("p_doctor") || null,
    is_returning_patient: chk("p_returning"),
    modality_restriction: val("p_modality") || null,
    age_exception: chk("p_age_exc"), custom_price: numOrNull("p_price"),
  }});
  if (ok) flash("Paciente salvo ✓");
}
```

Substituir por (acrescenta `booking_fee_waived: chk("p_fee_waived")`):

```javascript
async function savePatient(pid) {
  const ok = await post(`/api/atendente/paciente/${pid}`, { data: {
    name: val("p_name"), social_name: val("p_social_name"), birth_date: val("p_birth"), patient_cpf: val("p_cpf"),
    email: val("p_email"), doctor_id: val("p_doctor") || null,
    is_returning_patient: chk("p_returning"),
    modality_restriction: val("p_modality") || null,
    age_exception: chk("p_age_exc"), custom_price: numOrNull("p_price"),
    booking_fee_waived: chk("p_fee_waived"),
  }});
  if (ok) flash("Paciente salvo ✓");
}
```

- [ ] **Step 3: Conferir que o dado do paciente chega ao template com o campo**

O template lê `patient.booking_fee_waived`. Confirmar que o objeto `patient` entregue pela rota de contexto inclui a coluna. Rodar:

Run: `grep -rn "booking_fee_waived" dashboard/attendant_routes.py dashboard/attendant_db.py`
Expected: aparece a coluna sendo lida/retornada no `SELECT` do paciente (ex.: `select("*")` ou lista explícita). Se o SELECT for por lista de colunas e `booking_fee_waived` não estiver nela, adicionar. Se for `select("*")`, nada a fazer.

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/atendente.html
git commit -m "feat(painel): checkbox de taxa de reserva sempre isenta na aba Paciente"
```

---

### Task 3: Verificação final e suíte completa

**Files:**
- (nenhum arquivo novo; verificação)

- [ ] **Step 1: Rodar a suíte inteira**

Run: `uv run pytest --tb=short`
Expected: PASS (sem regressão; inclui o novo teste da Task 1)

- [ ] **Step 2: Conferência visual do painel (manual)**

Subir o painel localmente, abrir a aba "Paciente" de um paciente e confirmar:
- o checkbox "Taxa de reserva sempre isenta" aparece junto de "Paciente retornante" e "Exceção de idade";
- marcar, clicar "Salvar paciente", recarregar e o checkbox continua marcado (persistiu no banco);
- desmarcar e salvar também persiste.

Se o ambiente local de banco não estiver disponível, registrar isso na entrega e pedir à clínica a validação no painel real. Não marcar como verificado o que não foi visto rodar.

- [ ] **Step 3: Commit final (se houver ajuste do Step 3 da Task 2)**

```bash
git add -A
git commit -m "chore(painel): ajustes finais da isenção permanente de taxa"
```

---

## Self-Review

- **Cobertura do spec:** os três pontos do spec estão cobertos — whitelist (Task 1), checkbox + payload (Task 2), sem rota/migration nova (confirmado). Testes do spec: whitelist aceita o campo (Task 1) e a guarda do `_filter` não regride (já coberta por `test_patient_fields_whitelist_contains_core_fields` + o `_filter` existente; o campo novo entra na whitelist sem afrouxar o filtro).
- **Placeholders:** nenhum. Todo código está escrito por extenso.
- **Consistência de nomes:** id do checkbox `p_fee_waived`, coluna `booking_fee_waived`, chave do payload `booking_fee_waived` — batem entre template, JS e whitelist.
- **Ponto de atenção herdado do spec:** os três lugares precisam andar juntos; a Task 1 (whitelist) é a que de fato faz salvar, por isso vem primeiro e com teste.
