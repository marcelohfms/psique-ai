# Design: campo de Nome Social no cadastro de pacientes

## Contexto

A clínica atende pacientes que, em algum momento (não necessariamente no cadastro
inicial), pedem para serem chamados por um nome diferente do nome civil registrado
(caso comum: pacientes trans). Hoje não existe onde guardar essa preferência — o único
nome no cadastro é `patients.name`, que é também o identificador usado para
CPF/prontuário/conciliação financeira e não deve ser alterado.

## Objetivo

Adicionar um "Nome Social" por paciente que:
- nunca é perguntado proativamente pela Eva — só é registrado quando o próprio
  paciente/contato menciona espontaneamente;
- passa a ser usado quando a Eva se dirige ao paciente na conversa;
- aparece também no Google Calendar e no e-mail interno enviado à clínica, ao lado do
  nome civil (para uso do médico/atendente);
- pode também ser definido manualmente pela atendente no dashboard;
- nunca substitui `patients.name` como identificador para matching/CPF/lógica interna —
  o nome civil continua sendo reconhecido normalmente (ex: quando a mãe do paciente se
  refere a ele pelo nome civil durante um agendamento).

Fora de escopo: planilha de pagamentos (`app/google_sheets.py`) continua usando
`financial_name or name`, sem mudança.

## Modelo de dados

Nova coluna nullable em `patients`, mesmo padrão de `financial_name`:

```sql
alter table patients add column social_name text;
```

Migration nova em `supabase/migrations/`, seguindo o padrão dos arquivos existentes
(`20260615_create_patients_contacts.sql` etc.).

`social_name` é sempre opcional — não entra em `is_registration_complete`
(`app/database.py:247-296`) e não faz parte do fluxo obrigatório de `collect_info`.

## Detecção e persistência (nova tool)

Nova tool `set_social_name` em `app/graph/tools.py`, chamável em qualquer estágio da
conversa (não só `collect_info`):

- Parâmetro: `social_name: str`.
- Resolve o paciente ativo com a mesma lógica de resolução de paciente já usada por
  outras tools de estágio livre (ex: `request_document`).
- Sanitiza o valor em código (função nova, determinística — não depende do LLM seguir
  instrução de prompt) antes de gravar: remove sufixos de idade (`\d+\s*anos?`),
  conteúdo entre parênteses, e frases de parentesco/justificativa comuns
  (ex: "minha filha", "é como me chamam"). Ver seção de motivação abaixo.
- `UPDATE patients SET social_name = ... WHERE id = ...`.
- Loga em `events` (`social_name_set`), mesmo padrão de auditoria de outras mudanças de
  cadastro.

**Motivação da sanitização em código:** a regra de limpeza de nomes hoje só existe como
instrução de prompt (`app/graph/prompts.py:149-152`, "LIMPEZA DE NOMES") e já falhou na
prática — um paciente teve o nome salvo como "Nome 11 anos" e precisou de correção
manual. Para `social_name`, a sanitização roda em código como camada adicional
(não como substituto da instrução de prompt, que continua orientando a Eva a já
mandar o valor limpo).

### Prompt (`app/graph/prompts.py`)

Nova instrução dizendo que, se o paciente/contato mencionar espontaneamente um nome
pelo qual prefere ser chamado, a Eva deve chamar `set_social_name` — e explicitamente
que ela **nunca** deve perguntar isso de forma proativa. Depois de setado, a Eva passa
a se dirigir ao paciente por esse nome nas mensagens seguintes.

## Onde o nome social aparece

| Canal | Comportamento |
|---|---|
| Conversa (Eva ↔ paciente/contato) | Usa `social_name` sozinho quando presente; fallback pro `name` civil quando não há. |
| Google Calendar (`app/google_calendar.py`, summary/description) | `"{name} ({social_name})"` quando presente; senão só `name`. |
| E-mail interno à clínica (`_notify_clinic` em `app/graph/tools.py`) | Mesmo formato: `"{name} ({social_name})"` quando presente. |
| Matching/identificação (resolução de paciente, lógica de "nome canônico" em `confirm_appointment`, disambiguação entre múltiplos cadastros no mesmo telefone) | Continua comparando por `name` civil (fonte de verdade); passa a aceitar `social_name` como alias adicional na comparação, mas nunca o usa como identificador primário. |
| Planilha de pagamentos (`app/google_sheets.py`) | Sem mudança — `financial_name or name`. |
| Dashboard (`dashboard/templates/atendente.html`) | Novo campo "Nome Social" na seção Paciente, ao lado de "Nome". |

### Formato interno: Nome Civil (Nome Social)

Decisão deliberada: nome civil primeiro, nome social entre parênteses — não o
contrário. O nome civil é o identificador que casa com CPF/convênio/prontuário; mantê-lo
como primário reduz risco de descasamento em qualquer conferência/auditoria. O nome
social entre parênteses já comunica ao médico como chamar o paciente na consulta.

## Dashboard

- `dashboard/attendant_db.py`: adicionar `"social_name"` a `_PATIENT_FIELDS` (linha ~89).
- `dashboard/templates/atendente.html`: adicionar
  `${field("Nome Social", "p_social_name", patient.social_name)}` na seção Paciente
  (perto de "Nome", linha ~314), e `social_name: val("p_social_name")` no payload de
  `savePatient()` (linha ~367).
- Nenhuma rota nova — `update_patient` já é genérico via whitelist.

## Testes

Seguindo a estrutura existente (`CLAUDE.md`):
- `tests/test_tools.py`: nova suíte para `set_social_name` (chamada em qualquer estágio,
  sanitização de sufixos comuns, persistência, log de evento) + testes de
  `confirm_appointment`/Calendar/e-mail formatando `"{name} ({social_name})"` quando
  presente e só `name` quando ausente.
- `tests/test_process_message.py`: garantir que `set_social_name` não é chamada de forma
  proativa pelo fluxo de `collect_info` (não deve virar uma pergunta obrigatória).
- `tests/test_calendar.py`: formatação do summary/description do evento com/sem
  `social_name`.
- `dashboard/tests/`: teste do whitelist de campo novo em `attendant_db.update_patient`.
