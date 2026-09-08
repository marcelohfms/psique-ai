# Isenção permanente da taxa de reserva no painel da atendente

## Problema

Existe no banco a flag `patients.booking_fee_waived` (BOOL, default FALSE). Quando ela está ligada, o bot cria toda consulta nova do paciente já isenta da taxa de reserva, então a cobrança automática e o auto-cancelamento nunca disparam para esse paciente. Isso já é respeitado pelo bot em `app/graph/tools.py:1539-1561`.

O que falta é uma forma de a atendente ligar essa flag pelo painel. Hoje o único botão de isenção no painel é "Isentar taxa", que age em UMA consulta específica (`appointments.booking_fee_waived`), não no paciente. Para marcar um paciente como sempre isento, alguém precisa mexer direto no banco.

## Objetivo

Adicionar na aba "Paciente" do painel um checkbox "Taxa de reserva sempre isenta" que liga/desliga `patients.booking_fee_waived`.

## Comportamento

Ligar a flag vale só daqui pra frente. Toda consulta futura do paciente nasce sem taxa. Consulta que já está agendada com taxa pendente não muda por causa disso. Para isentar a taxa de uma consulta já marcada, a atendente continua usando o botão "Isentar taxa" daquela consulta, como já é hoje. Esse é o mesmo alcance que o bot já aplica ao ler a flag no momento do agendamento.

## Não faz parte do escopo

Não mexe no botão "Isentar taxa" por-consulta (`appointments.booking_fee_waived`, `dashboard/payments.py:702`). Não mexe em `custom_price` nem no caso de cortesia (`custom_price == 0`). Não isenta retroativamente taxas pendentes.

## Mudanças

Três pontos, sem rota nova e sem migration nova, porque a coluna já existe e o endpoint de salvar paciente é genérico.

1. `dashboard/templates/atendente.html`, aba "Paciente" (perto das linhas 360-361, junto de "Paciente retornante" e "Exceção de idade"): adicionar `checkbox("Taxa de reserva sempre isenta", "p_fee_waived", patient.booking_fee_waived)`. E incluir `booking_fee_waived: chk("p_fee_waived")` no payload de `savePatient()` (perto das linhas 404-410).

2. `dashboard/attendant_db.py`: adicionar `"booking_fee_waived"` ao set `_PATIENT_FIELDS` (linhas 161-165). Sem isso o `_filter` descarta o campo em silêncio e o salvamento não persiste.

3. Nenhuma mudança de rota. `POST /api/atendente/paciente/{pid}` (`dashboard/attendant_routes.py:103`) já persiste campos genéricos do paciente via `attendant_db.update_patient()`.

## Testes

Seguir a estrutura de testes existente em `tests/` (conferir se já há arquivo de teste do dashboard e seguir o padrão; criar um só se não houver cobertura do painel).

- `update_patient` aceita e persiste `booking_fee_waived` quando presente no payload.
- `update_patient` continua descartando campos fora da whitelist (guarda de segurança do `_filter` não regride).

## Riscos

Baixo. Coluna já existe e já é lida pelo bot. O único ponto de atenção é lembrar dos três lugares juntos: se esquecer a whitelist, o checkbox aparece mas não salva.
