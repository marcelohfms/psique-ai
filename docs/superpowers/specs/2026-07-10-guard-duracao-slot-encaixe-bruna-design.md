# Guard de duração do slot (Dr. Júlio) + encaixe de 40min (Dra. Bruna)

Data: 2026-07-10

## Contexto / bug de origem

Caso **Bernardo Rabelo Porto Ferreira** (contato 5581991320003, mãe Mônica),
1ª consulta com Dr. Júlio, menor de idade.

- A mãe optou por agendar os dois momentos da 1ª consulta **separadamente**
  (1ª hora com responsáveis, 2ª hora com o paciente).
- A Eva buscou corretamente **slots de 1h** e ofereceu 18:00 e 19:00 na quinta
  23/07 (agenda de quinta à noite do Dr. Júlio: **18:00–20:00**).
- A mãe escolheu 19:00. Mas no `confirm_appointment` o modelo passou
  `slot_duration_minutes=120` sem `session_note`, gravando **19:00–21:00** —
  um bloco de 2h que **estoura o fecho das 20:00**.

Causa raiz de código: o guard de grade em `confirm_appointment`
(`app/graph/tools.py`) valida **apenas o início** do slot
(`(sh*60+sm) <= slot_min < (eh*60+em)`), nunca `início + duração`. Um bloco de
120min começando às 19:00 (início válido) mas terminando às 21:00 (fora da
janela) passa e é persistido. O `get_available_slots` já barra isso corretamente
(`while current + slot_delta <= window_end`); só o `confirm_appointment` deixava
passar.

## Item 1 — Dr. Júlio: rejeitar booking que estoura a janela

**Onde:** `confirm_appointment`, dentro do bloco `if not force_encaixe:` que já
valida a grade (ramo de dia normal e ramo de dia de exceção). Encaixes
(`force_encaixe=True`) permanecem isentos.

**O quê:** apenas quando `doctor == "julio"`, além de exigir que o **início**
caia numa janela, exigir que o **slot inteiro** caiba numa única janela:

```
slot_end_min = slot_min + slot_duration_minutes
fits = any((sh*60+sm) <= slot_min and slot_end_min <= (eh*60+em)
           for sh, sm, eh, em, _ in day_wins)
```

Se não couber, retorna instrução interna (não enviar ao paciente) avisando que
não há bloco daquela duração seguido e mandando a Eva chamar
`get_available_slots` de novo / oferecer sessões separadas — **sem gravar**.

**Escopo Júlio-only:** decisão do usuário. As consultas normais da Dra. Bruna são
sempre 1h on-grid e sempre cabem, então o check nunca dispararia para ela de
qualquer forma; restringir a Júlio evita qualquer efeito colateral na lógica
dela.

## Item 2 — Dra. Bruna: encaixe começando a :20 vira 40min

**Motivação:** a Dra. Bruna costuma fazer um encaixe às 13:20 nas sextas. Com
60min ele termina 14:20 e o busy-check bloqueia o slot regular das 14:00. Em
40min ele termina 14:00 e mantém o slot das 14h livre.

**Onde:** `confirm_appointment`, logo após validar `force_encaixe` e ter
`doctor`/`start` resolvidos.

**O quê:**

```python
# Encaixe da Bruna começando a :20 termina no topo da hora (40min) para não
# bloquear o slot da hora seguinte (ex: sexta 13:20 -> 14:00, mantém o 14h livre).
if force_encaixe and doctor == "bruna" and start.minute == 20:
    slot_duration_minutes = 40
```

Vale para **qualquer dia e qualquer hora**, desde que seja encaixe da Dra. Bruna
com início a `:20` (decisão do usuário: "qualquer encaixe :20 dela"). A duração
sobrescrita (40) propaga para o evento no Calendar, `end_time` e busy-check. A
assinatura da tool mantém `Literal[60, 120]`; só a variável interna é
sobrescrita.

## Testes (`tests/test_tools.py`, mockando Calendar/Supabase)

1. Júlio 120min às 19:00 numa quinta (janela 18–20) → **rejeitado**, não grava,
   retorna instrução de rebuscar.
2. Júlio 120min às 18:00 numa quinta (cabe 18–20) → **aceito**.
3. Júlio 60min às 19:00 (sessão separada / split) → **aceito**.
4. Bruna encaixe (`force_encaixe=True`) início 13:20 → `end_time` = 14:00 (40min).
5. Bruna encaixe on-grid 13:00 → segue 60min (inalterado).

## Fora de escopo

- Reforço no prompt para o modelo não passar 120min no fluxo "separado" — o guard
  de código é a garantia; ajuste de prompt fica para outra hora se necessário.
- Qualquer mudança na lógica de agenda da Dra. Bruna além do clamp do encaixe :20.
