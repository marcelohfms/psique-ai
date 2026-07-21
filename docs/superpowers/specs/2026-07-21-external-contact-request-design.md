# Design: solicitação de contato médico ↔ terceiro externo

## Contexto

Caso concreto: Renata Monteiro (contato 5581996962165), mãe/responsável de Suzi Monteiro
Viana (paciente de Dr. Júlio, consulta 22/07/2026 09h), pediu por três vezes (08/07, 20/07,
21/07) que Dr. Júlio entrasse em contato com a psicóloga externa de Suzi antes da consulta.
A Eva respondeu "vou repassar" nas três vezes, mas nenhum pedido foi de fato registrado em
lugar nenhum (nem tabela, nem planilha, nem e-mail) — padrão já conhecido como "Phantom
Handoff" na memória do projeto.

## Objetivo

Dar à Eva duas tools novas para que pedidos desse tipo — paciente/responsável pedindo que o
médico entre em contato com um terceiro externo ligado ao cuidado do paciente (psicólogo,
terapeuta, outro médico, escola etc.) — sejam de fato registrados e cheguem ao médico por
e-mail, tanto no pedido inicial quanto em cobranças subsequentes do paciente.

## Escopo

- Cobre qualquer terceiro externo (não só psicólogos).
- Cria uma tabela nova e genérica no banco (`requests`), pensada para acomodar outros tipos
  de solicitação no futuro, mas **usada por enquanto só pelas duas tools novas** deste
  projeto. Migrar `request_document` / `register_refund_request` /
  `request_registration_update` para essa tabela fica fora de escopo.

## Tabela nova: `requests`

```sql
create table requests (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  type text not null,             -- ex: "contato_terceiro"
  content text not null,          -- rótulo curto, ex: "Contato com terceiro: psicóloga"
  metadata jsonb not null         -- campos específicos do tipo, ver abaixo
);

alter table requests enable row level security;
```

Para `type = "contato_terceiro"`, `metadata` contém: `phone`, `doctor_id`, `patient_name`,
`third_party_role`, `third_party_name`, `third_party_contact` (opcional), `reason`.

RLS habilitado sem policy, mesmo padrão de
`supabase/migrations/20260713_enable_rls_patient_tables.sql` (backend usa `service_role`,
ignora RLS — isso só fecha a porta pra uso futuro/acidental da chave anon).

Diferenças deliberadas em relação à `documents` (que cumpre papel parecido para
documentos): tem `created_at` real (a `documents` não tem, e o `nudge_doctor_document`
usa `id` como proxy de ordem); tem `type` desde já pensando em extensão futura, mesmo
com um único valor por enquanto; sem coluna `status` — não há tool ainda que marque
conclusão, então essa coluna não é criada agora (adicionar quando houver uso real).

## Tool 1 — `request_external_contact`

```python
@tool
async def request_external_contact(
    third_party_role: str,      # ex: "psicóloga", "terapeuta", "outro médico", "escola"
    third_party_name: str,      # ex: "Bruna Psicóloga"
    reason: str,                # motivo/contexto, ex: "acompanhamento de Suzi antes da consulta de 22/07"
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
    third_party_contact: str = "",  # telefone/e-mail do terceiro, se informado
) -> str:
    """Registra um pedido do paciente para que o médico entre em contato com um terceiro
    externo ligado ao cuidado do paciente (psicólogo, terapeuta, outro médico, escola etc.)
    antes ou em torno de uma consulta. Chame na primeira vez que o paciente pedir isso.
    """
```

Comportamento:
1. Resolve `patient_name`, `phone`, `doctor_id`/e-mail do médico — mesmo lookup já usado
   por `request_document` (`state` + fallback em `get_user_by_phone` + tabela `doctors`).
2. Insere linha em `requests` (`type="contato_terceiro"`, `metadata` com os campos acima).
3. `log_event("external_contact_requested", phone, {...})`.
4. Envia e-mail ao médico via `send_external_contact_request_email` (novo, em
   `app/email_sender.py`).
5. `_notify_clinic(...)` — mesmo helper genérico já usado por `request_document`.
6. Retorna string de confirmação para a Eva repassar ao paciente.

## Tool 2 — `nudge_external_contact`

```python
@tool
async def nudge_external_contact(
    patient_message: str,
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
    """Notifica o médico por e-mail quando o paciente cobra sobre um pedido de contato com
    terceiro externo (psicólogo, terapeuta etc.) já registrado anteriormente.
    Chame SOMENTE quando o paciente reforçar/cobrar sobre um pedido já feito
    (ex: 'o Dr. Júlio ainda não falou com a psicóloga'). NÃO use para criar um pedido novo.
    """
```

Comportamento: busca a última linha em `requests` para esse telefone
(`metadata->>phone`, sem precisar filtrar por `type` já que a tabela é dedicada a esse
propósito) → reenvia e-mail ao médico via `send_external_contact_nudge_email` (novo),
incluindo `patient_message` e o tempo decorrido desde o pedido original (via
`created_at`) → `log_event("external_contact_nudge_sent", ...)`.

## E-mails

Duas funções novas em `app/email_sender.py`, mesmo esqueleto SMTP das existentes
(`send_document_request_email` / `send_document_nudge_email`): retornam silenciosamente
se faltar config de SMTP ou e-mail do médico.

- `send_external_contact_request_email(doctor_key, doctor_email, patient_name, patient_age, phone, third_party_role, third_party_name, third_party_contact, reason)`
- `send_external_contact_nudge_email(doctor_key, doctor_email, patient_name, patient_age, phone, third_party_role, third_party_name, patient_message, requested_at)`

## Wiring

- `app/graph/nodes.py`: importar as duas tools novas e adicioná-las à lista `TOOLS`
  (linhas 27-35).
- `app/graph/prompts.py`: novo bloco de instrução perto do bloco de
  `request_document`/`nudge_doctor_document` (~linhas 780-824), explicando quando chamar
  cada tool nova — e replicar no bloco duplicado (~linhas 1090-1114), já que o arquivo
  hoje mantém essas instruções em dois lugares.

## Testes

`tests/test_tools.py`: casos mockados (insert no Supabase, email sender, lookup em
`doctors`) para `request_external_contact` e `nudge_external_contact`, seguindo a
estrutura dos testes já existentes para `request_document`/`nudge_doctor_document`.

## Fora de escopo

- Migrar `documents` (request_document/nudge_doctor_document), refunds ou registration
  updates para a tabela `requests`.
- Qualquer mecanismo de "concluído"/status para os pedidos (sem coluna `status`, sem tool
  de confirmação).
- Atualizar o evento no Google Calendar (decisão explícita do usuário: não precisa).
