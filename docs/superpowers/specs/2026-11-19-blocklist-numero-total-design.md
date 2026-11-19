# Blocklist total de número: buraco negro antes da Eva

Data: 2026-11-19
Autor: Ayexa (com Claude)

## Intenção e porquê

O WhatsApp da Clínica Affectus (`+558191812399`) manda comprovantes de terceiros o
tempo todo, como imagem sem legenda. Ele não é paciente e nunca deve ser atendido pela
Eva. O `manual_hold` não resolve: ele silencia a Eva no recebimento, mas o replay de
comprovante perdido ainda reinjeta e, depois do PR #352, manda e-mail para a clínica a
cada comprovante. Queremos um buraco negro para esse número: nada de Eva, nada de
e-mail, nada gravado.

## Objetivo

Toda mensagem de um número da blocklist é descartada no ponto mais cedo possível, antes
de qualquer efeito colateral (Vision, upload no Drive, gravação em `messages`, e-mail
para a clínica). Fica só uma linha de log de auditoria para rastrear que chegou e foi
bloqueada.

## Não-objetivos

Não criar categoria por tag/etiqueta do Chatwoot nem botão no painel agora — isso fica
para depois, se a clínica quiser gerenciar a lista sozinha. Não mexer no `manual_hold`
(que continua sendo o "desligar a Eva para um paciente", reversível, com e-mail no
replay). Não tentar bloquear por "medico"/"representante" — são contatos legítimos que a
Eva precisa atender.

## Constraints

- A entrada principal é o webhook da Meta, que NÃO enxerga as etiquetas do Chatwoot. Por
  isso o bloqueio tem que ser um dado nosso (constante no código), não uma etiqueta.
- O número chega em formatos diferentes (com/sem o 9, com/sem `+`, com/sem sufixo
  `@s.whatsapp.net`). A checagem normaliza antes de comparar.

## Approaches

1. **Constante no código (escolhida).** Uma lista `BLOCKED_NUMBERS` em `app/phone.py`
   (módulo-folha, sem import de `app.*`, usável em todos os canos). Explícito, auditável
   no git, trivial de estender. Para mudar: commit + deploy (raro, é o número da
   clínica). Minha recomendação, aceita pela Ayexa.
2. **Variável de ambiente.** `EVA_BLOCKED_NUMBERS` no Easypanel. Muda sem mexer no
   código, mas ainda precisa redeploy e é menos auditável. Descartada por ora.
3. **Etiqueta no Chatwoot espelhada / botão no painel.** Mais flexível para a clínica,
   bem mais trabalho (campo novo + espelho/UI + checagem). Fica para depois.

## Como vai funcionar

`app/phone.py` ganha:

- `BLOCKED_NUMBERS: frozenset[str]` — forma canônica com 9 (55 + DDD + 9XXXXXXXX).
  Começa com `{"5581991812399"}` (Clínica Affectus).
- `is_blocked_number(phone) -> bool` — normaliza (tira `+`, sufixo, resolve o 9 pelas
  variantes) e devolve True se bate com a lista.

Checagem em quatro pontos (defesa em profundidade):

1. **Webhook da Meta** (`_handle_payload`): logo após o dedup, ANTES do `extract_message`
   (que roda o Vision). Ganho principal: nem lê a imagem, nem sobe ao Drive, nem grava.
2. **Webhook do Chatwoot**: logo depois de extrair o telefone.
3. **Cron de reconciliação** (`reconcile`): pula o candidato bloqueado antes de baixar,
   classificar ou alertar.
4. **Backstop em `process_message`**: `if is_blocked_number(phone): return` no topo.
   Garante silêncio total mesmo por caminho interno (recuperação de restart, replay do
   eva-ativa).

## Testes

- `is_blocked_number` nas variantes (canônico, 12 dígitos sem o 9, com `+`, com sufixo,
  número legítimo = False).
- Backstop do `process_message`: número bloqueado retorna sem tocar grafo nem gravação.
- `reconcile` pula número bloqueado (sem alerta, sem replay).
- No padrão dos testes existentes, no CI antes da main. Worktree própria + PR.

## O que seria fracasso

A Eva voltar a responder esse número por qualquer cano. Bloquear sem querer um número
legítimo. Deixar vazar e-mail ou gravação de um número bloqueado.
