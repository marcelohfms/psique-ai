# eva-ativa executa nota-comando da atendente

Data: 2026-09-08
Branch: `feat/eva-ativa-executa-nota-comando`

## Problema

A atendente comanda a Eva por nota privada no Chatwoot (ex.: "Eva, agendar consulta
de Maria Cecília no dia 24/09 às 14h, presencial, com Dr. Júlio"). Hoje esse comando
se perde quando é enviado com a conversa pausada (etiqueta `eva-inativa`):

1. A nota chega com a Eva pausada → `_handle_attendant_note` sai cedo em
   `_eva_paused_for_phone` e grava `attendant_note_suppressed_paused`. A nota nunca é
   executada.
2. Ao reativar com `eva-ativa`, o ramo de replay em `_apply_eva_label_action` só
   reprocessa a última mensagem **do paciente**, nunca a nota; e quando a nota é mais
   recente que a mensagem do paciente ele **pula** de propósito
   (`eva_ativa_replay_skipped`), pra não reabrir turno antigo e mandar confirmação/PIX
   em dobro (caso 5581979037093).

Resultado: o comando cai no vão entre as duas travas. Nada é agendado.
Caso real: Juliana/Maria Cecília 5581987330022, conv #375, 08/09/2026 (eventos
`attendant_note_suppressed_paused` + `eva_ativa_replay_skipped` a 2s de distância).

## Objetivo

Ao adicionar a etiqueta `eva-ativa`, se a última coisa da conversa for uma
**nota-comando** (nota privada da atendente que começa chamando a Eva pelo nome), a Eva
executa esse comando pelo caminho normal de nota, em vez de pular.

## Fluxo de trabalho esperado (atendente)

1. Com a conversa pausada (`eva-inativa`), a atendente escreve e envia a nota-comando
   completa (ex.: "Eva, agende...").
2. A atendente adiciona a etiqueta `eva-ativa`.
3. A Eva reativa, vê que a última coisa da conversa é a nota-comando, e a executa.

A ordem importa: a nota tem que estar completa e enviada **antes** de adicionar a
`eva-ativa`. Isso evita a corrida em que a Eva responderia o paciente enquanto a
atendente ainda digita. Não haverá auto-execução da nota no momento em que ela chega
pausada — a execução acontece só na reativação, quando a nota já está pronta. (Decisão
explícita da Ayexa: não auto-executar na chegada, justamente pela corrida de digitação.)

## Regra de decisão ("o último vence")

No ramo `_EVA_ACTIVE_LABEL in added` de `_apply_eva_label_action`, após
`_resume_bot_for_patient`, a Eva compara o horário da última mensagem do paciente com o
horário da última nota privada humana:

- **Nota-comando é a mais recente, começa com "Eva" e está pendente** (nota ≥ mensagem do
  paciente; começa com "Eva"; foi suprimida e ainda não rodou — ver trava abaixo) →
  executa o comando via `_handle_attendant_note`; NÃO reproduz a mensagem do paciente;
  grava o marcador `attendant_note_resumed_executed`.
- **Nota-comando é a mais recente e começa com "Eva", mas NÃO está pendente** (já rodou
  antes, ou rodou ao vivo e nunca foi suprimida) → não executa, não reproduz o paciente
  (`eva_ativa_replay_skipped`). Protege o PIX em dobro.
- **Mensagem do paciente é a mais recente** (paciente falou depois da nota) → reproduz a
  mensagem do paciente (comportamento atual); NÃO executa o comando. A atendente
  reenvia a nota depois se ainda quiser rodar o comando.
- **Última nota não começa com "Eva"** (recado interno) → não faz nada
  (`eva_ativa_replay_skipped`, comportamento atual).
- **Sem nota, com mensagem do paciente** → reproduz a mensagem do paciente
  (comportamento atual, intacto).
- **Reativação removendo `eva-inativa`** (ramo `in removed`) → continua sem reproduzir
  nem executar nada (comportamento atual, intacto). O replay/execução mora só no ramo de
  adicionar `eva-ativa`.

## O que conta como "começa com Eva"

- Ignora espaços em branco no começo.
- Não diferencia maiúscula/minúscula.
- A primeira palavra tem que ser exatamente "Eva" seguida de um limite de palavra
  (vírgula, espaço, dois-pontos, exclamação, fim da string, etc.).
- Regex: `^\s*eva\b` (case-insensitive).
- Valem: "Eva, agende...", "Eva agende...", "eva: agenda...", "Eva! ...".
- Não valem: "Evaristo ligou", "Evangelina confirmou".

## Detecção e trava anti-execução-dupla

- O **texto** e o **horário** da nota vêm de `get_last_patient_message`, que já busca as
  mensagens do Chatwoot. Hoje ela devolve `last_note_at` mas não o texto da nota — será
  estendida para devolver também `last_note_content` (o conteúdo completo da última nota
  privada escrita por atendente humana, `private=True` e `sender.type == "user"`).
- **A nota só é executada se estiver PENDENTE**, ou seja, se foi suprimida enquanto a Eva
  estava pausada e ainda não rodou. Esse é o ponto-chave que evita reexecução: uma nota
  que já foi executada ao vivo (Eva ativa) grava `attendant_note_received`, não
  `attendant_note_suppressed_paused`, então nunca é considerada pendente.
- **Como saber se está pendente (casado por conteúdo, não por horário):**
  - Ao suprimir a nota (Eva pausada), o evento `attendant_note_suppressed_paused` passa a
    guardar o **conteúdo completo** da nota (hoje corta em 300 caracteres; o corte sai
    para esse evento). Ele é o registro de "essa nota foi suprimida".
  - Após executar na reativação, grava-se `attendant_note_resumed_executed` com o mesmo
    conteúdo completo. Ele é o registro de "essa nota já rodou".
  - `pendente = existe attendant_note_suppressed_paused com esse conteúdo E NÃO existe
    attendant_note_resumed_executed com esse conteúdo`.
  - O casamento é por conteúdo exato (não por timestamp) porque o `created_at` do webhook
    e o `created_at` da API de mensagens do Chatwoot podem estar em formatos diferentes
    (ISO vs epoch); o texto é estável entre as duas fontes.
- Isso protege contra remover e adicionar a `eva-ativa` de novo (a segunda vez encontra o
  `attendant_note_resumed_executed` e não repete) e contra o caso histórico de PIX em
  dobro (nota executada ao vivo nunca foi suprimida, logo nunca é considerada pendente).

## Execução da nota

Reusa o caminho existente `_handle_attendant_note(payload)`. Monta-se um payload
sintético (espelhando o endpoint `/admin/attendant-note`, app/main.py) com o texto da
nota, o `conversation_id`, `message_type=1`, `private=True` e um `sender.type="user"`.
Como `_resume_bot_for_patient` já rodou, `_eva_paused_for_phone` é falso e a nota passa
normalmente pelo pipeline, exatamente como se tivesse chegado com a Eva já ativa.

## Arquivos afetados

- `app/chatwoot.py` — `get_last_patient_message` passa a devolver `last_note_content`.
- `app/database.py` — novo helper `get_events_by_type(phone, event_type)` para ler os
  eventos de supressão/execução (usado pela trava de pendência).
- `app/main.py`:
  - helper puro `_looks_like_eva_command(text)` (regex `^\s*eva\b`).
  - helper `_note_command_pending(phone, note_text)` (usa `get_events_by_type`).
  - helper `_execute_attendant_note_on_resume(phone, conversation_id, note_text)` (monta o
    payload sintético e chama `_handle_attendant_note`; grava `attendant_note_resumed_executed`).
  - ramo `_EVA_ACTIVE_LABEL in added` de `_apply_eva_label_action`: nova lógica de decisão.
  - ramo de supressão em `_handle_attendant_note`: guardar o conteúdo completo da nota no
    evento `attendant_note_suppressed_paused` (tirar o corte de 300 caracteres).
- `tests/test_webhook.py` — testes novos + atualização do teste existente
  `test_conv_updated_eva_ativa_skips_replay_when_note_is_newer`.

## Testes (tests/test_webhook.py)

1. **Nota-comando é a última e está pendente** → `_handle_attendant_note` é chamada com o
   texto da nota; a mensagem do paciente NÃO é reproduzida; `attendant_note_resumed_executed`
   é gravado.
2. **Mensagem do paciente mais nova que a nota** → paciente é reproduzido; o comando NÃO
   roda.
3. **Última nota não começa com "Eva"** → nada roda, nada é reproduzido.
4. **Nota-comando é a última mas já foi executada** (só existe `attendant_note_resumed_executed`
   com aquele conteúdo, ou a nota rodou ao vivo e nunca foi suprimida → não pendente) → não
   executa de novo, não reproduz o paciente. Cobre a regressão do PIX em dobro
   (caso 5581979037093). Atualiza o teste existente `..._skips_replay_when_note_is_newer`.
5. **Sem nota, com mensagem do paciente** → reproduz a mensagem do paciente (comportamento
   atual).
6. **Reativação removendo `eva-inativa`** → não reproduz nem executa nada.
7. **`_looks_like_eva_command`** (teste unitário puro) → aceita "Eva, ...", "eva: ...",
   "Eva ..."; rejeita "Evaristo ...", "" e texto que não começa com Eva.

Mocks: `get_last_patient_message`, `_handle_attendant_note`, `_resume_bot_for_patient`,
`get_events_by_type` (a trava de pendência) e `buffer_push` (o caminho de replay do paciente).

## Fora de escopo (YAGNI)

- Não muda o comportamento da nota quando a Eva já está ativa (segue executando toda
  nota como instrução).
- Não muda a supressão da nota na chegada com a Eva pausada.
- Não muda o ramo de remoção da `eva-inativa`.
- Não trata conflito de ordem com aviso à atendente (a regra "o último vence" já decide).
