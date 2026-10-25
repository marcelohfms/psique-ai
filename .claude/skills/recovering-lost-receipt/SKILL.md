---
name: recovering-lost-receipt
description: Use quando a Ayexa pedir para avaliar, investigar ou corrigir uma conversa em que o paciente (ou responsável) mandou o comprovante da taxa de reserva e a Eva não registrou — comprovante perdido, dropado, "sumiu", imagem sem legenda, receipt_drop_alerted, consulta cancelada pelo cron unpaid_booking_fee mesmo com pagamento feito, taxa paga mas booking_fee_paid_at vazio.
---

# Recuperando comprovante perdido (taxa de reserva)

## Overview

Imagem de comprovante sem legenda às vezes é dropada na ingestão da Meta: nada em
`messages`, nada em `events`, e 2h depois o cron `unpaid_booking_fee` cancela a consulta.
A correção é sempre manual, por script one-off, e reproduz o que `register_payment` faria,
reusando os helpers reais do app. Fonte do comprovante é o **Chatwoot** (anexo da conversa),
nunca a tabela `messages`.

**Princípio:** diagnostique tudo sozinho primeiro; à Ayexa pergunte UMA coisa, o horário.

## Diagnóstico (só leitura)

Rode scripts locais com `import scripts._dnsfix` (DNS do Supabase no Mac). Modelos:
`scripts/_check_558197441626_{messages,chatwoot,gcal_img}.py`.

| O quê | Onde | O que procurar |
|---|---|---|
| Contato, vínculo, paciente | `contacts` → `patient_contacts` → `patients` | telefone com e sem o 9; `patients.name` (não `contacts.name`, costuma ser null) |
| Consulta | `appointments` por `patient_id` | status (`scheduled` vs `canceled`), `appointment_id`, `booking_fee_paid_at`, `start_time` UTC |
| Quem cancelou | `events` do phone | `appointment_canceled` com `reason: unpaid_booking_fee` = cron; `receipt_drop_alerted` = rede de reconciliação viu a queda |
| Comprovante | Chatwoot `GET /conversations/{id}/messages` → `attachments[]` | `id`, `data_url`, hora; baixe com `follow_redirects=True` e calcule o md5 |
| Já registrado? | `events` `payment_receipt_registered` com `metadata.file_md5` | se achar, não registre de novo |
| Horário ainda livre? | `_get_busy(svc, agenda_id, start, end)` no GCal do médico | 0 eventos = livre; evento antigo pode estar deletado (`showDeleted=True`) |
| Eva pausada? | label `eva-inativa` na conversa, `contacts.active` | é decisão da atendente, não reative |

Leia o comprovante baixado com a ferramenta Read (é imagem): dele saem `PAYER` (nome do
pagador), valor e hora do Pix. `PAID_AT` = hora do Pix impressa no comprovante; se ilegível, o
`created_at` da mensagem no Chatwoot. Sem anexo no Chatwoot, peça a imagem à Ayexa.

Classifique:
- **Caso A**: consulta ainda `scheduled`, só falta a taxa → registrar pagamento.
- **Caso B**: cancelada pelo cron, horário livre → reativar a MESMA linha + registrar pagamento.
- **Caso C**: cancelada e horário ocupado → `UPDATE` com `status=pending_reschedule` e `booking_fee_paid_at` (igual ao ramo "slot taken" de `register_payment`), sem GCal; a clínica escolhe novo horário e aí vira Caso B.

## Confirmação com a Ayexa (obrigatória antes de qualquer escrita)

Uma pergunta: "recolocar em <data hora> <modalidade> com <médico>? horário está livre". Avise
que, com a janela de 24h fechada, o aviso sai pelo template `pagamento_taxa_recebido`, que
**não cita a data**. Ofereça "outro horário" e "fazer tudo menos avisar o contato".
Se o diagnóstico deixou dúvida de fato (dois filhos vinculados, valor diferente de R$ 100,
comprovante ilegível, md5 já registrado em outra consulta), ela entra como item da MESMA
pergunta, com o que você já apurou. Não pergunte o que o banco responde.

## Correção

Copie `fix_template.py` desta pasta para `scripts/_fix_<nome>_<telefone>_taxa_oneoff.py`
(`scripts/_*.py` é gitignored: fica como registro local, não commite). Preencha as
constantes com o diagnóstico. Rode em DRY RUN, depois `APPLY=1`, depois `APPLY=1 DO_SEND=1`.

O template cobre o Caso B. Caso A: apague o bloco "1. GCal + reativação" e troque a guarda
de status para `!= "scheduled"`. Caso C: troque o bloco 1 pelo `UPDATE` de `pending_reschedule`.

O template faz, nessa ordem, com os helpers reais:

1. Guardas: status esperado, `booking_fee_paid_at` vazio, nenhuma consulta ativa do paciente, md5 ausente em `events`, horário livre no GCal, md5 baixado == esperado.
2. Caso B: `create_event` (skill psique-calendar-sync) e `UPDATE` na mesma linha: `status=scheduled`, `appointment_id` novo, `booking_fee_paid_at`, `payment_reminder_sent_at=None`. Nunca `INSERT` de linha nova (skill reschedule-payment-migration). Evento `appointment_reactivated_manual`.
3. `upload_image` → `build_receipt_filename` → `rename_file` (skill payment-receipt-drive-format) → `append_payment_receipt` (PIX, tipo taxa) → `log_event("payment_receipt_registered", {file_md5, payer, drive_link})` → espelho `[imagem]: COMPROVANTE DE PAGAMENTO ... — registrado pela atendente [drive_link:...]` em `messages` → `send_clinic_notification_email`.
4. Aviso: janela aberta (última mensagem do contato no Chatwoot há menos de 24h, `last_incoming_at`) → `send_text`; fechada → `send_template_message(conv, "pagamento_taxa_recebido", "pt_BR", "UTILITY", {"1": primeiro nome, "2": "R$ 100,00", "3": "a consulta de <Paciente>"}, texto)`. Espelhe o texto em `messages` como `assistant`.
5. `add_private_note` na conversa resumindo o que foi feito. Eva continua como estava.

Se o APPLY falhar no meio, o script já imprimiu cada passo concluído: rodar de novo aborta na
guarda, então termine à mão só os passos que faltaram (o evento `appointment_reactivated_manual`
e a linha reativada dizem até onde chegou).

## Depois

Releia banco, GCal e Chatwoot para provar o estado final; rode o script de novo e mostre
que aborta na guarda. Atualize a memória do caso (`caso-<nome>-...md`) como RESOLVIDO e,
se for sessão filha, deixe nota na pasta compartilhada.

## Common Mistakes

| Erro | Por quê | Correto |
|---|---|---|
| Procurar o comprovante em `messages` | dropado = não gravou | anexo no Chatwoot |
| Baixar `data_url` sem `follow_redirects` | Chatwoot redireciona ao storage | `httpx.AsyncClient(follow_redirects=True)` |
| Criar linha nova em `appointments` | duplicata ativa, taxa não migra | `UPDATE` da linha cancelada |
| Esquecer `payment_reminder_sent_at=None` | cron acha que já cobrou | zerar ao reativar |
| `contacts.name` no evento/mensagem | costuma ser null | `patients.name`; primeiro nome do contato vem do Chatwoot |
| `send_text` fora da janela | Meta descarta em silêncio, sem erro | template via Chatwoot |
| Prometer a data no template | texto fixo aprovado pela Meta | avise a Ayexa que a data não vai |
| Reativar a Eva ou tirar `eva-inativa` | decisão da atendente | só relate |
| Worktree/PR para o one-off | não é código do app | script local ignorado pelo git |
| Mais de uma pergunta à Ayexa | o diagnóstico responde o resto | só o horário |
