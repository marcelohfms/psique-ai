# Varredura de mensagem perdida no buffer — design

Data: 2026-11-19
Aprovado por: Ayexa (caminho e números, 19/11/2026)

## Intenção e o porquê

Quando o paciente responde (ex.: "sim" confirmando o agendamento), a mensagem é
gravada em `messages` e o processamento pelo grafo é agendado por um timer em
memória (`loop.call_later` em `app/buffer.py`). Se o processo reinicia ou o timer
se perde nessa janela, o turno some: a mensagem fica no banco sem resposta, sem
erro, sem evento. A Eva fica muda até alguém da clínica mandar uma nota privada
que força o reprocessamento.

Caso de referência: Marina Leal Ribeiro, +5581988197076, 12/11/2026,
conversation 82. "sim" das 14:06:19 UTC perdido; a Eva só reagiu às 14:36 depois
da nota "confirma" da atendente. Ver memory `mensagem-perdida-buffer-nota-privada-destrava`.

Objetivo: recuperar sozinho essas mensagens perdidas, sem depender da nota manual.

## Objetivos

- Reprocessar automaticamente a última mensagem do paciente que ficou sem resposta
  da Eva, venha a perda de onde vier (timer perdido, restart tardio, task que
  morreu em `buffer._run` sem relançar).
- Recuperação em poucos minutos, muito antes das 2h do prazo da taxa.
- Zero resposta dobrada e zero intromissão em conversa que está calada de propósito.

## Não-objetivos

- Não tornar o buffer durável (fila persistida no banco). É mudança grande no
  caminho quente, risco alto, desnecessária pro volume da clínica.
- Não substituir a recuperação de boot (`_recover_messages_lost_to_restart`) nem o
  drain de shutdown. Esta varredura é uma rede adicional que cobre o que os dois
  não alcançam (perda sem restart, ou restart depois dos 10 min da janela de boot).
- Não mexer no reconcile de comprovantes (`app/receipt_reconciliation.py`), que é
  outra rede, só de anexos.

## Anti-objetivos (o que seria uma falha mesmo "funcionando")

- Responder de novo uma mensagem que já foi respondida (ou que está só lenta,
  ainda em retry de rate limit).
- Fazer a Eva falar numa conversa transferida/pausada (`active=False` /
  `manual_hold`), atropelando a atendente.
- Uma varredura que derruba o app ou que, num candidato ruim, perde os demais.

## Restrições

- `process_message` depende de estado montado no `lifespan`: o grafo com o
  checkpointer do Postgres (`graph_module.chatbot`, setado em `app/main.py:272`) e
  os locks de telefone em memória (`app/buffer.py:_phone_locks`). O módulo tem um
  `chatbot = build_graph()` default (sem checkpointer do Postgres) em
  `app/graph/graph.py:120`. Logo, a varredura NÃO pode rodar num subprocesso-cron
  (como o reconcile de comprovantes): num subprocesso a Eva trataria toda conversa
  como nova. Tem que rodar DENTRO do processo do app.
- A mensagem do paciente já está em `messages` (gravada no handler do webhook,
  `app/main.py:1061`, antes do buffer). `process_message` NÃO re-grava a mensagem
  do usuário, então reprocessar não duplica linha.

## Abordagens consideradas

1. **Varredura periódica in-process (ESCOLHIDA).** Uma tarefa de fundo iniciada no
   `lifespan`, rodando a cada 2 min. Generaliza a recuperação de boot: acha
   telefones cuja última mensagem é do paciente, sem resposta, mais velha que um
   piso, dentro de uma janela, e reempurra pelo caminho normal. Roda o tempo todo,
   pega a perda de qualquer causa, sem depender de restart. Reaproveita as travas
   que já existem (portão de pausa + dedup de checkpoint).
   - Prós: fecha o buraco de raiz; pequena; reusa código e segurança existentes.
   - Contras: roda para sempre no processo; consulta o banco a cada tick.

2. **Buffer durável (fila persistida).** Conserta a arquitetura, mas é grande,
   mexe no caminho quente e cria novos modos de falha. Descartada: exagero pro
   volume.

3. **Só alargar a janela dos 10 min do boot.** Continua dependendo de restart; no
   caso da Marina não houve restart em seguida. Não resolve. Descartada.

## Como funciona (o que foi decidido)

Novo módulo `app/lost_message_recovery.py` com uma função pura de decisão e uma de
execução, mais o fio no `lifespan`.

### Detecção de candidato

Uma varredura (`sweep(now)`), a cada `LOST_MSG_SWEEP_INTERVAL` (padrão 2 min):

1. Puxa de `messages` as linhas da janela `[now - window, now]`
   (`window` = `LOST_MSG_SWEEP_WINDOW_MIN`, padrão 360 min = 6h), ordenadas por
   `created_at`.
2. Reduz à última linha por telefone (mesma lógica do
   `_recover_messages_lost_to_restart`).
3. Candidato = última linha com `role == 'user'`, com conteúdo, cujo `created_at`
   seja mais velho que `now - floor` (`floor` = `LOST_MSG_SWEEP_FLOOR_MIN`, padrão
   5 min). O piso evita cutucar algo que ainda pode estar em processamento legítimo
   (debounce 9s + invoke + retry de rate limit).
4. Idempotência: pula se já existe evento `lost_message_replayed` para esse
   telefone com `created_at` posterior ao da mensagem candidata (não reempurra duas
   vezes a mesma mensagem enquanto o reprocesso ainda não terminou).

### Execução

Para cada candidato, com try/except individual (um ruim nunca derruba a rodada):

- Chama `process_message(phone, text)` — SEM `bypass_pause` (respeita a pausa) e
  SEM `save_message` (a mensagem já está no banco).
- Registra `log_event("lost_message_replayed", phone, {message_created_at, preview})`.

### Por que é seguro (reusa trava existente, não inventa)

- **Pausa/transferência:** `process_message` sai cedo em
  `_eva_paused_for_phone` (`active=False` ou `manual_hold`) — `app/main.py:814`.
  `transfer_to_human` grava `active=False` (`app/graph/tools.py:5492`). Então
  conversa transferida ou pausada vira no-op, mesmo com janela de 6h.
- **Resposta dobrada:** `process_message` tem dedup cross-process
  (`app/main.py:873-883`): se a última HumanMessage do checkpoint já é esse texto,
  ele sai sem responder. Então, se a mensagem foi processada (mesmo lenta, mesmo em
  retry), o reprocesso é no-op. O lock de telefone (`app/main.py:823`) serializa a
  chamada da varredura contra um turno em voo.

### Fio no lifespan

Em `app/main.py`, dentro do `lifespan` (junto do `start_scheduler`), iniciar uma
`asyncio.Task` que roda um loop: dorme o intervalo, chama `sweep(now)`, nunca deixa
exceção vazar. Cancelar/aguardar no shutdown, do lado do `stop_scheduler` e do
`_drain_buffer_on_shutdown`. Não roda com `uvicorn --reload`/testes (mesmo portão
`SCHEDULER_LIVE` que o agendador usa), pra não disparar em dev.

## Parâmetros (aprovados)

- Piso: 5 min (`LOST_MSG_SWEEP_FLOOR_MIN`).
- Intervalo: 2 min (`LOST_MSG_SWEEP_INTERVAL_MIN`).
- Janela: 6h (`LOST_MSG_SWEEP_WINDOW_MIN`).
- Recuperação esperada: no máximo ~7 min após a mensagem.

## O que fica a cargo do builder

- Nome exato das env vars e defaults (acima).
- Reuso vs. cópia da redução "última linha por telefone" (hoje inline em
  `_recover_messages_lost_to_restart`); extrair um helper se ficar limpo.
- Forma exata do guard de idempotência (consulta a `events`).
- Testes: unit da decisão de candidato (pura, sem rede) e testes mockados da
  execução (pausa = no-op, dedup = no-op, caso perdido = reprocessa, candidato ruim
  não derruba a rodada). Seguir a estrutura de `tests/` do projeto.
