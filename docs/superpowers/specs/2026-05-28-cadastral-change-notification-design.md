# Design: Notificação de Alteração Cadastral

**Data:** 2026-05-28  
**Status:** Aprovado

## Contexto

Quando um paciente solicita explicitamente a alteração de um dado do seu cadastro (e-mail, CPF, nome, data de nascimento, etc.), Eva precisar notificar a atendente por e-mail para que ela realize a atualização manual no sistema — exceto para e-mail, que Eva já consegue atualizar diretamente no banco.

Atualmente não existe um fluxo dedicado para isso: `save_patient_email` só é usado na coleta inicial durante o agendamento, e `transfer_to_human` desativa o bot — comportamento indesejado neste caso.

## Escopo

- Qualquer campo cadastral solicitado explicitamente pelo paciente
- O bot permanece ativo após a notificação
- A notificação vai por e-mail (via `_notify_clinic` / `send_clinic_notification_email`)
- `save_patient_email` não é alterado — continua sendo usado apenas no fluxo de agendamento

## Arquitetura

### Nova ferramenta: `request_registration_update`

**Arquivo:** `app/graph/tools.py`

```python
@tool
async def request_registration_update(
    field: str,       # ex: "email", "CPF", "nome", "data de nascimento"
    new_value: str,   # novo valor informado pelo paciente
    state: Annotated[dict, InjectedState],
    config: RunnableConfig,
) -> str:
```

**Comportamento:**

1. Se `field` for `"email"` (case-insensitive): chama `upsert_user(phone, {"email": new_value})` para atualizar o banco imediatamente.
2. Para qualquer campo: envia e-mail à clínica via `_notify_clinic` com:
   - **Assunto:** `"Solicitação de alteração cadastral — <nome do paciente>"`
   - **Corpo:** nome do paciente, telefone, campo solicitado, novo valor, data/hora (fuso Recife)
3. Retorna mensagem de confirmação ao LLM, ex:  
   `"Pedido de alteração de [campo] registrado. A equipe irá processar em breve."`
4. Bot permanece ativo.

### Atualização no prompt do sistema

**Arquivo:** `app/graph/prompts.py`

Adicionar nos dois templates (`EXISTING_PATIENT_SYSTEM` e `NEW_PATIENT_SYSTEM`), na seção de ferramentas:

```
- ALTERAÇÃO CADASTRAL: quando o paciente solicitar explicitamente a correção ou atualização
  de qualquer dado cadastral (e-mail, CPF, nome, data de nascimento, etc.), chame
  request_registration_update com o campo (field) e o novo valor (new_value) informado.
  NÃO use essa ferramenta durante o fluxo normal de coleta de dados para agendamento —
  apenas quando for uma solicitação de alteração de dado já existente.
```

## Fluxo de dados

```
Paciente: "quero alterar meu e-mail para X"
  → LLM chama request_registration_update(field="email", new_value="X")
  → upsert_user(phone, {"email": "X"})          # apenas para email
  → _notify_clinic(email com detalhes da alteração)
  → retorna confirmação ao LLM
  → Eva confirma ao paciente: "Seu pedido foi registrado..."
  → Bot continua ativo
```

## Tratamento de erros

- Falha no e-mail: logar o erro, mas retornar sucesso ao LLM (a atualização do banco, quando aplicável, já terá ocorrido). O `_notify_clinic` já tem try/except silencioso.
- `field` não reconhecido como email: apenas notifica por e-mail, sem tentar atualizar o banco.

## Testes

**`tests/test_tools.py`:**

- `test_request_registration_update_email`: mock `upsert_user` + `send_clinic_notification_email` → verifica que ambos são chamados com os valores corretos
- `test_request_registration_update_other_field`: mock `send_clinic_notification_email` → verifica que `upsert_user` **não** é chamado para campo não-email
- `test_request_registration_update_returns_confirmation`: verifica que a string de retorno menciona o campo solicitado

**`tests/test_process_message.py`:**

- Mensagem `"quero alterar meu e-mail para novo@email.com"` → verifica que o LLM chama `request_registration_update` (e não `save_patient_email`)

## O que não muda

- `save_patient_email`: mantido sem alteração para uso exclusivo no fluxo de agendamento
- `transfer_to_human`: não é utilizado neste fluxo
- Estrutura do banco de dados: nenhuma migração necessária
