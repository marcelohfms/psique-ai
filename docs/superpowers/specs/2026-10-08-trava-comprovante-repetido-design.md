# Trava de comprovante repetido

Data: 08/10/2026. Origem: caso Miguel Costa Loureiro (5581991012815). Em 04/09 a atendente deu baixa na consulta de 08/10 pelo painel com o mesmo arquivo da consulta de 03/09 (md5 idêntico). Nada no sistema compara um comprovante novo com os já usados.

## Objetivo

Detectar quando um arquivo de comprovante idêntico a um já registrado é usado de novo, nos dois caminhos de entrada, e impedir a baixa silenciosa.

## Impressão do arquivo

Usamos o md5 dos bytes do arquivo. É o mesmo valor que o Drive expõe em `md5Checksum`, o que permite preencher o histórico sem baixar arquivos. Não é uso de segurança, só identidade de arquivo.

O md5 passa a ser gravado em `metadata.file_md5` de todo evento `payment_receipt_registered`, junto com `appointment_dt` (data e hora da consulta) quando disponível. A busca é `events` com `event_type = payment_receipt_registered` e `metadata->>file_md5 = <md5>`, sem filtro de telefone (o mesmo arquivo em outra família também é suspeito). Devolve o registro mais antigo: paciente, consulta, data do registro e link.

Falha na leitura (Supabase ou Drive) não bloqueia o pagamento. A trava é fail-open, como as outras guardas de `register_payment`.

## Painel (dashboard)

O md5 é calculado com `hashlib` sobre os bytes enviados, antes de subir ao Drive.

Painel do Chatwoot (`/api/atendente/pagamentos/{id}/pagar-com-comprovante`): se o md5 já existe e o formulário não traz `confirmar_duplicado=1`, a rota responde 409 com os dados do registro anterior e não grava nada nem sobe o arquivo. O JS mostra um `confirm()` com a mensagem e, se a atendente aceitar, reenvia com `confirmar_duplicado=1`.

Página completa de pagamentos (`/api/pagamentos/{id}/comprovante` e depois `/pagar`): a checagem acontece no upload, com o mesmo 409 e a mesma confirmação. O upload passa a devolver `file_md5`, que o JS repassa ao `/pagar` para entrar no evento.

Quando a atendente confirma, o registro segue normal e um evento `payment_duplicate_receipt_confirmed` guarda o md5, o appointment novo e o registro anterior.

## Eva (register_payment)

Para comprovante de imagem com `drive_link`, logo depois da guarda de processamento duplo, a Eva lê o `md5Checksum` do arquivo no Drive e procura registros anteriores com o mesmo md5 e `drive_link` diferente. Se achar, não registra nada, grava o evento `payment_duplicate_receipt_blocked`, avisa a clínica por e-mail (com os dois links) e devolve uma instrução interna: dizer ao paciente que esse comprovante já consta registrado para a consulta de tal data e que a equipe vai conferir, sem dizer que o pagamento foi confirmado.

A atendente resolve pelo painel, onde verá o aviso e poderá confirmar.

## Histórico

Script one-off `scripts/_backfill_receipt_md5.py`: para cada `payment_receipt_registered` com `drive_link` e sem `file_md5`, lê o `md5Checksum` no Drive e atualiza o metadata. Arquivos apagados ou inacessíveis são pulados e contados.

## Fora do escopo

Prints diferentes do mesmo PIX (arquivos diferentes) não são detectados. Comparar o código da transação fica para depois, se aparecer caso real.

## Testes

`tests/test_tools.py`: Eva bloqueia com md5 repetido; registra quando o md5 é novo; registra quando a leitura do Drive falha; grava `file_md5` no evento.
`dashboard/tests/test_payments.py` e testes de rota: 409 sem confirmação, registro com confirmação e evento de confirmação, fluxo normal com arquivo novo, `file_md5` no evento.
