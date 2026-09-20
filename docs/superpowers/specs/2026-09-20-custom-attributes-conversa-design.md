# Custom attributes do paciente na conversa do Chatwoot

Data: 2026-09-20
Status: aprovado (design), aguardando plano de implementação.
Origem: `AUDITORIA-ESTRATEGICA-2026-09.md`, seção 4 (Chatwoot subutilizado) e 7.2/8.

## Contexto e problema

Hoje a atendente não consegue, olhando a conversa no Chatwoot, saber o essencial do paciente. Para descobrir médico, próxima consulta ou se a taxa foi paga, ela abre o painel embutido ou outro sistema. O backend já tem todos esses dados. O Chatwoot 4.16 (community) suporta custom attributes nativos, exibidos na lateral da conversa, e o projeto não usa nenhum (zero ocorrências de `custom_attribute` no código).

## Objetivo

Expor, na lateral da conversa, quatro atributos do paciente, gravados como custom attributes de contato no Chatwoot, atualizados por cron. Aditivo, fora do caminho quente do webhook.

## Não-objetivos

- Não tocar o webhook nem o grafo.
- Não gravar atributos na conversa (usamos contato, ver decisões).
- Não expor todos os dados possíveis: só os quatro combinados.
- Não criar views/filtros a partir dos atributos neste momento.

## Decisões tomadas no brainstorming

- Quatro atributos: **médico**, **próxima consulta**, **taxa de reserva**, **é retornante**.
- Referem-se sempre ao **paciente da consulta mais próxima** do telefone; a próxima consulta traz o nome junto. Sem consulta futura, médico/retornante vêm do paciente mais recente.
- Gravados **no contato** (persistem na lateral entre conversas resolvidas/reabertas), não na conversa.
- Atualizados por **cron a cada 30 minutos**, nada no fluxo ao vivo.
- **Gravados mesmo com o contato pausado** (eva-inativa/manual_hold): custom attribute não manda nada ao paciente, é só informação para a atendente, e é quando ela assumiu a conversa que mais precisa. Isto difere do nudge, que respeita a pausa.
- Só chama a API do Chatwoot quando algum valor muda (memória via evento), para proteger do rate limit.

## Os quatro atributos (valores)

Todos do tipo texto. As `attribute_key` (usadas na API e no cadastro do painel) entre parênteses:

- **Médico** (`medico`): "Dr. Júlio" ou "Dra. Bruna".
- **Próxima consulta** (`proxima_consulta`): "22/09/2026 14:00 online — João" (data, hora, modalidade e primeiro nome do paciente). Sem consulta futura: "sem consulta futura".
- **Taxa de reserva** (`taxa_reserva`): "Paga", "Pendente" ou "Isenta". Sem consulta futura: vazio.
- **É retornante** (`retornante`): "Retornante" ou "Primeira vez".

Regras de valor:
- Taxa: `booking_fee_waived` verdadeiro ou paciente cortesia (`custom_price == 0`) → "Isenta"; `booking_fee_paid_at` preenchido → "Paga"; senão → "Pendente".
- Médico: mapa `{julio: "Dr. Júlio", bruna: "Dra. Bruna"}` a partir de `doctor_id`.
- Retornante: `is_returning_patient` verdadeiro → "Retornante"; senão → "Primeira vez".

## Modelo de dados e seleção do paciente

Fonte dos fatos, tudo de tabelas existentes:

- **Consulta mais próxima do telefone**: appointments com `status='scheduled'` e `start_time >= agora`, do(s) paciente(s) ligado(s) ao contato; escolher a de menor `start_time`. Dela vêm data/hora, `modality`, `patient_id`, `doctor_id`, `booking_fee_paid_at`, `booking_fee_waived`.
- **Nome e retornante do paciente**: da tabela `patients` (via `patient_id` da consulta mais próxima). Primeiro nome para a `proxima_consulta`.
- **Sem consulta futura**: usar o paciente mais recente do telefone via `get_user_by_phone(phone)` (nome, médico, retornante); `proxima_consulta` = "sem consulta futura", `taxa_reserva` = vazio.

## Conjunto de contatos sincronizados (candidatos)

A cada rodada, o candidato é a UNIÃO de:

1. Contatos que têm ao menos uma consulta futura agendada (`status='scheduled'`, `start_time >= agora`). Enumerados por uma query global em appointments, mapeando `contact_id`/`patient_id` para o telefone via `contacts`.
2. Contatos já sincronizados antes (lidos do evento de memória, ver abaixo), para atualizar quando a consulta passou ou foi cancelada (aí caem para "sem consulta futura").

Isso mantém o conjunto pequeno e os valores sempre corretos, sem varrer o banco inteiro.

## Idempotência e memória entre rodadas

O cron do GitHub Actions nasce sem estado. Guardar em `events` um evento por contato (`contact_attributes_synced`, metadata com o dict dos quatro valores e o `contact_id`). A cada rodada:

- Montar os quatro valores atuais.
- Ler o último evento; se o dict for igual, **não** chamar o Chatwoot.
- Se mudou, resolver o `contact_id` do Chatwoot (busca; cria se não existir), gravar via API e registrar o novo evento.

Falha na API do Chatwoot não grava o evento, então a próxima rodada tenta de novo.

## Camada Chatwoot

Nova função em `app/chatwoot.py`:

- `set_contact_custom_attributes(contact_id: int, attrs: dict) -> None`: `PATCH /api/v1/accounts/{account}/contacts/{contact_id}` com corpo `{"custom_attributes": attrs}`, via o `_request` com retry já existente e o `_headers()` (token com permissão de contato).
- Helper para resolver o `contact_id` por telefone reusando `_search_contact`/`_create_contact` já existentes (uma função fina tipo `find_or_create_contact_id(phone) -> int`).

Nada toca as labels de controle nem o gate de pausa.

## Passo operacional (uma vez, fora do código)

Criar os quatro atributos no painel do Chatwoot, em Administração > Atributos personalizados, como **atributos de contato**, com as `attribute_key` exatas: `medico`, `proxima_consulta`, `taxa_reserva`, `retornante`. Sem isso os valores gravados pela API não aparecem na lateral. Entra no plano de teste, não no código.

## Componentes

- **Novo** `app/patient_attributes.py`: lógica pura — `build_attributes(facts) -> dict` (monta os quatro valores) e `needs_attr_change(new, last) -> bool`. Sem I/O.
- **Novo** `scripts/sync_contact_attributes.py`: o cron — enumera candidatos, monta os valores, reconcilia (só grava quando muda), registra a memória.
- **Novo** `.github/workflows/sync_contact_attributes.yml`: agenda a cada 30 min.
- **Modificar** `app/chatwoot.py`: `set_contact_custom_attributes` + resolução de `contact_id`.
- **Testes**: `tests/test_patient_attributes.py` (lógica pura), `tests/test_sync_contact_attributes.py` (cron com Supabase/Chatwoot mockados), e um teste da função nova em `tests/test_chatwoot.py`.

## Salvaguardas

- Só escreve no Chatwoot quando um valor muda (memória via evento) — protege do rate limit.
- Grava para contato pausado também (info da atendente, não mensagem ao paciente) — decisão explícita, diferente do nudge.
- Falha de API não grava a memória (retry na próxima rodada).
- Conjunto de candidatos limitado a quem tem consulta futura ou já foi sincronizado.
- Espaçamento/retry herdados do `_request` da camada Chatwoot.

## Riscos e mitigações

- **Multi-paciente por telefone**: resolvido escolhendo a consulta mais próxima e pondo o nome na `proxima_consulta`.
- **Volume de chamadas ao Chatwoot**: mitigado pela reconciliação só-quando-muda e pelo conjunto de candidatos enxuto.
- **`attribute_key` não cadastrada no painel**: valor gravado não aparece; mitigado pelo passo operacional e por conferência no primeiro run manual.
- **Resolver `contact_id` custa chamadas**: só acontece quando há mudança de valor (não em toda rodada).

## Trabalho fora de escopo (para depois)

- Views/filtros no Chatwoot usando estes atributos.
- Atributos adicionais (pendência de documento, de reembolso).
- Atualização em tempo real no webhook.
