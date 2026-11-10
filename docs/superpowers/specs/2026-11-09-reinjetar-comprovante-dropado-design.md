# Reinjetar comprovante dropado — a rede registra como se a Eva tivesse recebido

Data: 2026-11-09
Autor: Ayexa + Claude

## Intenção e o porquê

Comprovante de pagamento enviado como imagem sem legenda às vezes some na entrada: o
webhook da Meta não entrega a imagem ao nosso app, então nada é lido, nada é registrado
e a paciente não recebe resposta. A rede de segurança `app/receipt_reconciliation.py`
(cron `reconcile_dropped_receipts`, a cada 15 min) já detecta esses casos lendo os
anexos direto do Chatwoot — que recebe a mídia por um canal confiável, independente do
nosso webhook — mas hoje ela **só manda um e-mail** pedindo à clínica para lançar pela
painel. Resultado: quase todo dia um comprovante vira trabalho manual da atendente, e já
houve lançamento em dobro quando a atendente clica duas vezes (caso Marina/Miguel,
09/11/2026).

A dor que a clínica sente como "a Eva não lê comprovante" **não é leitura** — o modelo de
visão lê bem (zero `payment_receipt_unreadable` em 30+ dias). É o drop na ingestão. E como
a imagem está **sempre no Chatwoot**, os bytes do comprovante estão sempre disponíveis para
recuperação. Essa é a base da solução.

**O que muda:** em vez de só avisar, a rede pega o comprovante que está no Chatwoot e o
reinjeta no caminho normal da Eva, como se a mensagem tivesse acabado de chegar pela Meta.
A Eva faz exatamente o que faria num dia em que a Meta não droppa: lê a imagem, sobe pro
Drive, chama `register_payment` (com todas as travas que já existem) e responde a paciente.
Nenhuma lógica de pagamento nova. Reaproveita o motor que já funciona.

## Objetivos

- A rede de reconciliação, ao achar um **comprovante de pagamento** sem rastro, reinjeta a
  imagem no pipeline normal de ingestão da Eva, em vez de só mandar e-mail.
- O registro reusa `app/media.describe_image_bytes` (classificação + upload Drive + releitura
  do valor) e o grafo/`register_payment` exatamente como no caminho da Meta — mesmas travas de
  md5 repetido, escolha de consulta, pagador terceiro, valor fora do padrão.
- A paciente recebe a mesma mensagem de confirmação que a Eva mandaria.
- Idempotência: cada anexo é reinjetado **no máximo uma vez**, mesmo que o cron rode de novo
  antes do registro deixar rastro.
- Um candidato com falha (OpenAI 429, download, etc.) nunca derruba a rodada; a rede segue
  pelos demais, como hoje.

## Não-objetivos

- **Não** consertar a causa raiz do drop na ingestão (precisa de logs de produção do Easypanel
  para saber se é o webhook da Meta que não chega, o download que falha, ou a trava
  anti-duplicata entre os dois webhooks que barra o caminho de resgate do Chatwoot). Fica como
  investigação separada.
- **Não** mexer no caminho de **documento** médico. Nesta versão, documento sem rastro continua
  gerando só o e-mail de alerta de hoje. (Decisão minha de escopo, para limitar o raio de
  impacto; a reinjeção de documento é uma extensão natural depois.)
- **Não** mexer na painel nem no bug de clique-duplo da painel (assunto à parte).
- **Não** reativar a Eva nem tirar etiqueta `eva-inativa` — pausa é decisão da atendente.

## Anti-objetivos (o que tornaria isto um fracasso mesmo "funcionando")

- Registrar o mesmo comprovante duas vezes, ou registrar em cima de um lançamento que a
  atendente já fez pela painel.
- Mandar uma confirmação automática num contato que a clínica silenciou de propósito
  (`manual_hold`), atropelando uma decisão explícita.
- Auto-registrar um valor que a Eva normalmente **perguntaria** (pagador terceiro, valor
  diferente da taxa, chave mascarada): isso tem que continuar caindo no mesmo "perguntar/
  transferir" que a Eva já faz — nunca virar registro cego.
- Uma falha da reinjeção derrubar o cron e deixar os outros comprovantes sem rede.
- Deixar de registrar (e de confirmar) um comprovante enviado fora do horário de
  atendimento, fazendo a paciente perder a consulta para o cron de taxa não paga.

## Restrições

- Linguagem/stack do projeto (FastAPI + LangGraph + OpenAI), sem dependência nova.
- Testes obrigatórios (CLAUDE.md): atualizar os que quebrarem e cobrir o novo comportamento
  com teste de integração mockado (Chatwoot / Drive / OpenAI / Supabase), em `tests/`.
- Trabalho em git worktree própria, PR com CI verde antes de merge.

## Como vai funcionar (as decisões já tomadas)

### Gatilho e fluxo

1. `reconcile` já itera os anexos recebidos no Chatwoot entre 10 e 60 min de idade, pula os que
   têm rastro (`has_trace`) e os já tratados, baixa a imagem e classifica com
   `classify_image_raw`. Esse começo não muda.
2. Quando `kind == "payment"`: em vez de `_alert_clinic`, a rede **reinjeta** o comprovante.
   Quando `kind == "document"`: mantém `_alert_clinic` como hoje.
3. **Normalizar o telefone ANTES de qualquer coisa.** `list_recent_incoming_attachments`
   devolve `phone` em dígitos crus (`_strip_phone`, sem `@s.whatsapp.net`). O grafo
   (`thread_id`), o `register_payment` e o `send_text` operam na forma canônica
   `<dígitos>@s.whatsapp.net` com a normalização da variante do 9º dígito que o webhook do
   Chatwoot faz em `_extract_chatwoot_message` (`_phone_variants(...)[0] + "@s.whatsapp.net"`).
   A reinjeção TEM que aplicar a mesma normalização; passar o telefone cru abre uma thread
   fantasma, o `register_payment` não acha a consulta/contato e a confirmação vai pro vazio, em
   silêncio e sem erro.
4. Reinjetar = reproduzir o caminho de ingestão da Meta para uma imagem:
   - chamar `describe_image_bytes(img, phone=...)` — faz a classificação final, o upload pro
     Drive (bot vira dono, rename funciona) e a releitura reforçada do valor, devolvendo o texto
     `COMPROVANTE DE PAGAMENTO: ...[drive_link:...]` que a Eva receberia;
   - **se `describe_image_bytes` NÃO devolver texto de comprovante** (devolve `None` porque a 2ª
     classificação divergiu e roteou como documento/irrelevante): NÃO reinjetar no grafo —
     cair no `_alert_clinic` (e-mail de hoje), para o comprovante nunca ficar enterrado. O
     reconcile já tinha classificado como `payment` na 1ª passada; a divergência é rara
     (`classify_image_raw` é `gpt-5.2 temp=0`), mas quando acontece o ramo de documento da
     `describe_image_bytes` dispararia "Recebemos seu documento" por fora de tudo e NÃO
     registraria o pagamento — exatamente o que não pode. Por isso, sem comprovante na mão, é
     alerta, não reinjeção;
   - com o texto do comprovante em mãos: `save_message(phone_canonico, "user", texto)` — a
     linha-espelho `[imagem]:` que também serve de rastro para as próximas rodadas;
   - processar pelo grafo da Eva (o mesmo que `process_message` dispara), que chama
     `register_payment` com todas as travas, e responder a paciente.
5. Marcar o anexo como reinjetado para idempotência (ver abaixo).

### Idempotência (no máximo uma reinjeção por anexo)

- Antes de reinjetar, além de `already_alerted` e `has_trace`, checar um evento novo
  `receipt_drop_replayed` por `attachment_id` (mesmo padrão de `already_alerted`). Gravar esse
  evento assim que a reinjeção for disparada, **antes** do processamento do grafo. É
  incondicional: reinjetou uma vez, não reinjeta de novo — igual a uma mensagem real da Meta,
  que a Eva processa uma vez e não "retenta".
- Isso cobre o caso em que a Eva lê mas **não** registra na hora (ex.: decide perguntar o valor
  à paciente): não haveria `payment_receipt_registered` para o `has_trace` pegar, e sem o
  marcador próprio o cron reinjetaria de novo a cada 15 min. Como a mensagem da Eva sai em todos
  os estados (decisão de avisar sempre), a paciente ouve a pergunta e o caso não fica mudo. A
  linha-espelho `[imagem]:` gravada no passo 4 também passa a contar como rastro, mas o evento
  dedicado é a trava dura.
- O ramo de fallback (passo 4: `describe_image_bytes` devolveu `None`, caiu no `_alert_clinic`)
  **não** grava `receipt_drop_replayed`; usa a dedup de alerta que já existe
  (`already_alerted` / `receipt_drop_alerted`). Assim, se o anexo nunca virou comprovante na
  releitura, ele segue sendo um alerta tratado pela clínica, nunca uma reinjeção silenciosa.
- As travas internas de `register_payment` (md5 repetido entre telefones → `DUPLICATE_RECEIPT_MARKER`;
  drive_link reenviado na janela → `_receipt_already_registered`) continuam protegendo contra
  registro duplo caso a atendente tenha lançado pela painel em paralelo.

### Pausa da Eva — decisão: registrar e confirmar SEMPRE

O portão `_eva_paused_for_phone` hoje cala a Eva quando `manual_hold` ou `active=False`
(etiqueta `eva-inativa`). Para o comprovante que dropou, a reinjeção **ignora esse portão por
completo**: em qualquer estado — Eva ativa, `eva-inativa` ou `manual_hold` — a rede **registra
o pagamento e envia a mensagem de recebimento** à paciente.

Motivo (decisão da Ayexa, 09/11/2026): a paciente que mandou o comprovante fora do horário de
atendimento não pode perder a consulta. Quem de fato evita o cancelamento é o **registro** (o
cron de taxa não paga olha `booking_fee_paid_at`); a mensagem de recebimento funciona como um
aviso/tranquilização, igual a um lembrete, confirmando que a vaga está garantida. Por isso vale
mandar mesmo com a Eva pausada.

Importante: isto vale **apenas** para a reinjeção de comprovante feita por esta rede. O portão
`_eva_paused_for_phone` do caminho normal (mensagem de texto comum, nota da atendente) **não
muda** — a Eva continua calada na conversa pausada para tudo que não seja este resgate de
comprovante. O bypass é local a este fluxo.

### Observações de comportamento

- **Atraso:** por rodar no cron de 15 min sobre anexos de 10–60 min, a paciente recebe a
  confirmação entre ~10 e ~60 min depois de enviar o comprovante, não na hora. É muito melhor
  que o manual de hoje, mas não é instantâneo. Aceitável.
- **Texto da confirmação:** é a mensagem normal de sucesso do `register_payment` ("Comprovante
  recebido e registrado..."), idêntica ao caminho da Meta.
- **E-mail à clínica:** o `register_payment` já manda a notificação de pagamento à clínica no
  caminho normal; mantemos esse comportamento (não é o e-mail de "drop" antigo).

## O que fica para o builder decidir

- **Mecânica da reinjeção no grafo com pausa ignorada:** hoje `process_message` retorna cedo em
  `_eva_paused_for_phone`. O builder escolhe como reproduzir o caminho da Meta registrando
  e respondendo mesmo pausado — p.ex. um parâmetro de bypass em `process_message`, ou uma
  função de reinjeção que chama `describe_image_bytes` + grava o espelho + invoca o grafo
  direto. Requisito: usar o MESMO motor de `register_payment`, não reimplementar registro; e o
  bypass do portão de pausa tem que ser **local a este fluxo**, sem afetar o caminho normal.
- **Onde mora o código novo:** provavelmente uma função `replay_dropped_receipt(cand)` em
  `app/receipt_reconciliation.py` (ou um helper em `app/main.py` reaproveitado), mantendo o
  try/except por candidato.
- **Nome e forma exata do evento de idempotência** (`receipt_drop_replayed` com
  `{attachment_id, conversation_id, registered: bool}` é uma sugestão).
- **Cobertura de teste:** no mínimo — (a) comprovante dropado, Eva ativa, registra e responde;
  (b) `manual_hold` / `eva-inativa`, registra E responde (bypass do portão, local ao fluxo);
  (c) idempotência: segunda rodada não reinjeta; (d) md5 já registrado pela painel: não duplica;
  (e) documento continua só alertando; (f) candidato que lança exceção não derruba a rodada;
  (g) o caminho normal (`process_message`) continua calado em conversa pausada — o bypass não
  vazou; (h) `describe_image_bytes` devolve `None` na reinjeção (reclassificou como documento):
  cai no `_alert_clinic`, NÃO grava `receipt_drop_replayed`, não manda "recebemos documento";
  (i) telefone cru do candidato é normalizado para a forma canônica antes do grafo.
