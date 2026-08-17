# Design — `confirm_appointment` marca o irmão certo em contato multi-paciente

**Data:** 2026-08-14
**Autor:** Ayexa Tavares (via Claude)
**Origem:** varredura das conversas de 14/08/2026 — caso Renata Monteiro / Laila+Suzi Viana (5581996962165)

## Problema

Contatos que administram vários pacientes no mesmo telefone (23 contatos — famílias com
irmãos) podem ter uma consulta agendada sob o **paciente errado**.

No caso que motivou este design (14/08/2026):

1. A responsável Renata pediu explicitamente a consulta para a filha **Laila**
   ("Pra Laila por favor! Agora Laila é mais urgente").
2. A Eva mostrou o resumo "Paciente: **Laila**" e a responsável confirmou.
3. Mas a Eva chamou `confirm_appointment({})` **sem** `patient_name_override`. A resolução
   caiu no estado congelado (`user_db_id` e `patient_name` ambos = **Suzi**, do início da
   conversa, pois no estágio `patient_agent` nada re-resolve o paciente quando a responsável
   troca de irmão). A consulta de 17/08 14:00 nasceu sob **Suzi**.
4. O `register_payment` seguinte recebeu `override="Laila"` (a Eva o passa corretamente
   para pagamento), não achou a consulta de 17/08 sob a Laila, casou com uma consulta
   **completed antiga** dela (18/06) e retornou "já registrado" — deixando a taxa de R$100
   **sem lançamento** na consulta de 17/08.

Resultado: consulta sob o paciente errado + taxa não registrada (risco de auto-cancelamento
pelo cron de cobrança). *(O dado deste caso já foi corrigido manualmente via
`scripts/fix_laila_17ago_patient_and_fee_oneoff.py`; este design previne a recorrência.)*

## Causa-raiz

O mecanismo de override **já existe e está ligado**: `confirm_appointment` aceita
`patient_name_override` (tools.py:860) e o repassa para `_resolve_patient_for_booking`
(linhas 1003 e 1256) e para o `patient_name` do evento do Calendar (linha 1169). Quando o
override chega preenchido, o `_resolve_patient_for_booking` para contatos multi-paciente já o
prioriza corretamente (tools.py:186-189) sobre o `user_db_id`/`patient_name` congelados.

A falha é dupla e ambas do lado do LLM/instrução, não do mecanismo:

- **Assimetria no prompt:** o prompt instrui exaustivamente `patient_name_override` para o
  `register_payment` (prompts.py:591-685), mas **não há instrução equivalente** para o
  `confirm_appointment`. A Eva aprendeu a identificar o paciente na hora de pagar, não na de
  agendar.
- **Sem rede de segurança no tool:** quando o override vem vazio num contato multi-paciente,
  o `confirm_appointment` grava silenciosamente o paciente do estado congelado, que pode ser
  o irmão errado.

## Objetivo

Garantir que, para contatos com mais de um paciente, a consulta seja gravada sob o paciente
que a Eva confirmou na tela — sem adicionar atrito ao caminho normal de quem agenda.

Fora de escopo: contatos com um único paciente (comportamento inalterado); o fluxo de
`reschedule_appointment`; auto-sincronizar o estado quando a responsável troca de irmão.

## Solução

Duas partes complementares.

### (a) Prompt — instrução simétrica à do pagamento

Adicionar em `app/graph/prompts.py`, próximo ao passo de `confirm_appointment` (bloco ~511-534),
uma regra explícita:

> Quando o contato administra mais de um paciente (irmãos no mesmo telefone), **sempre** chame
> `confirm_appointment` com `patient_name_override` = exatamente o nome que aparece no resumo
> "Paciente: …" mostrado antes da confirmação. É o mesmo cuidado já exigido no `register_payment`.

### (b) Rede de segurança no `confirm_appointment`

No `confirm_appointment`, após resolver o paciente no ponto do Guard 0 (tools.py:1003), a rede
dispara quando:

- o contato tem **mais de um paciente** (`len(get_users_by_phone(phone)) > 1`), **e**
- o paciente resolvido **não veio de um match positivo e único do `patient_name_override`**.

**Este é o ponto crítico do gatilho** (corrigido em relação à primeira versão deste design). Não
basta checar "override vazio". O `_match_patient_by_name` (tools.py:107) devolve `None` sempre que
o nome não singulariza exatamente um paciente — override vazio, mas também **typo** ("Layla"),
**nome parcial**, ou **dois irmãos parecidos** ("Maria Clara" / "Maria Cecília"). E quando ele
devolve `None`, o `_resolve_patient_for_booking` cai no fallback `user_db_id` → `patient_name`,
que é o **estado congelado (o irmão errado)**. Se a rede olhasse só "override vazio", um override
que-não-casa escorregaria por esse fallback e gravaria o irmão errado do mesmo jeito. Portanto a
condição é: **o agendamento só prossegue se o override produziu um match único; caso contrário, a
rede assume — nunca deixa cair no `user_db_id` congelado.**

Quando a rede assume, o tool **não grava** o agendamento e retorna uma instrução interna (não
enviada ao paciente), no espírito do `register_payment` quando não identifica o paciente:

> *"[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Este contato administra mais de um paciente e não
> deu para identificar com segurança para qual a consulta é. Pergunte ao contato: 'Qual o nome
> completo do paciente para quem deseja agendar?' e rechame confirm_appointment com esse nome em
> patient_name_override."*

Pedir o **nome completo** (em vez de oferecer "é para X ou Y?") é deliberado: não expõe o nome do
outro irmão a quem segura o telefone e entrega uma string exata para o match civil.

Efeito na experiência de quem agenda:

- **Caminho normal (inclusive o caso Renata):** a Eva já sabe o nome (mostrou "Paciente: Laila"),
  passa o override, o match é único → grava a Laila. O paciente **não vê nada diferente** — só que
  a consulta nasce sob o paciente certo.
- **Dúvida real** (a responsável nunca disse o nome, ou disse algo que não singulariza): aparece
  **uma linha** — "Qual o nome completo do paciente para quem deseja agendar?" — e o agendamento
  só segue depois da resposta. Estritamente melhor que gravar no escuro.

Detalhes de implementação:

- A detecção multi-paciente reusa `get_users_by_phone(phone)` (já chamado dentro de
  `_resolve_patient_for_booking`); calcular `len(all_users) > 1` uma vez evita consulta redundante.
- A rede fica **antes** do `create_event`/insert, para nunca criar evento no Calendar sob o irmão
  errado.
- **Paridade guard × insert:** a decisão da rede tem que sair da MESMA resolução que o insert usa
  (via `_resolve_patient_for_booking`), não de uma segunda lógica paralela — foi a divergência
  entre esses dois pontos que gerou a classe de bug do #131. Na prática: derivar do resultado de
  `_resolve_patient_for_booking` se o paciente veio do override ou do fallback, e ramificar a
  partir disso nos dois pontos (1003 e 1256) de forma consistente.

## Testes (TDD, mockados — `tests/test_tools.py`)

Contato mockado com dois irmãos (Laila + Suzi), `state` com `user_db_id`/`patient_name` = Suzi:

1. **Override único respeitado:** `confirm_appointment(..., patient_name_override="Laila Monteiro
   Viana")` grava a consulta sob **Laila** (patient_id da Laila) e o evento sai "Consulta — Laila",
   mesmo com o estado congelado em Suzi.
2. **Rede dispara com override vazio:** `confirm_appointment(...)` **sem** override num contato
   multi-paciente retorna a instrução interna (pedir nome completo) e **não** cria evento nem
   insere agendamento.
3. **Rede dispara com override que não singulariza:** `confirm_appointment(...,
   patient_name_override="Layla")` (typo) ou um nome que casa com dois irmãos num contato
   multi-paciente **também** dispara a rede — NÃO cai no fallback `user_db_id` congelado. Este é o
   teste que trava o risco principal.
4. **Contato de um paciente inalterado:** `confirm_appointment(...)` sem override num contato
   com um único paciente grava normalmente (a rede não dispara) — garante ausência de regressão
   e de atrito novo.
5. **1ª consulta de menor dividida:** as duas chamadas de `confirm_appointment` do mesmo menor
   num contato multi-paciente, ambas com o override do menor, gravam as duas sessões sem a rede
   travar a segunda.

Rodar `uv run pytest tests/test_tools.py --tb=short` e a suíte completa antes de concluir.

## Riscos

- **Irmão errado por override que não casa (o principal):** typo, nome parcial ou irmãos de nome
  parecido fazem `_match_patient_by_name` devolver `None`, e o fallback grava o estado congelado.
  **Mitigado pelo gatilho corrigido** (a rede exige match único do override, não apenas override
  não-vazio) e pelo teste 3.
- **Divergência guard × insert (regressão do #131):** se a rede usar uma resolução paralela à do
  insert, os dois pontos podem discordar de novo. Mitigado exigindo que a decisão saia do mesmo
  `_resolve_patient_for_booking`.
- **Interação com a 1ª consulta de menor dividida:** a rede passa a exigir o override nas duas
  chamadas; o prompt precisa mandar passar o nome nas duas. Coberto pelo teste 5.
- **Agendamento por instrução da atendente sem nome:** num contato multi-paciente, a rede passa a
  bloquear e pedir o nome em vez de gravar. É o comportamento desejado, mas é uma mudança no fluxo
  dirigido pela atendente — registrada aqui de propósito.
- **Regressão em contato de um paciente:** mitigada pelo gate `len(all_users) > 1` e pelo teste 4.
- **Diluição do prompt:** uma regra a mais concorre pela atenção do LLM; risco baixo por ser
  simétrica à do pagamento e porque um override "sobrando" num contato de 1 paciente é ignorado.
- **Custo/latência:** quando a rede dispara, é um turno interno a mais — só nos ~23 contatos
  multi-paciente e só quando o override falta. Desprezível.
- **LLM ainda esquecer o override:** agora é seguro — a rede converte o esquecimento num pedido de
  nome, nunca num agendamento errado.
