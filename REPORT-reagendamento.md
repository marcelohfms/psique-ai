# Relatório: remarcação conta por consulta

## Antes / depois
mark_reschedule_in_progress (app/graph/tools.py ~2211):
- Antes: `.eq("phone", phone_clean).eq("event_type","appointment_rescheduled")` + 2 `.or_` (initiated_by, fee_paid)
- Depois: `.eq("metadata->>appointment_id", appointment_id).eq("event_type","appointment_rescheduled")` + os mesmos 2 `.or_`. Variável phone_clean (sem outro uso) removida.

Backstop em reschedule_appointment (~2779):
- Antes: `.eq("phone", _phone_clean)...`
- Depois: `.eq("metadata->>appointment_id", appointment_id)...` (appointment_id ainda é o atual, a reatribuição do pending ocorre depois, ~2838).
Comentários atualizados para escopo por consulta. Logging, trava de 24h e textos intocados.

## TDD
Novos testes em tests/test_tools.py: test_mark_reschedule_count_scoped_by_appointment_not_phone (verifica eq appointment_id e ausência de eq phone) e test_mark_reschedule_blocked_when_same_appointment_already_rescheduled. Com o código antigo (tools.py stashed) o teste de escopo FALHA; com a correção passa.
Suíte completa: 1832 passed.

## Ressalva
O backstop de reschedule_appointment não ganhou teste dedicado (a mudança é idêntica à do mark). Filtros or_ (clinic e fee_paid=false) não foram alterados, e o teste existente de fee_paid continua passando.
