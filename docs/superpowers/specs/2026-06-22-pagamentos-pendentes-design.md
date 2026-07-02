# Página de Pagamentos Pendentes

**Data:** 2026-06-22  
**Status:** Aprovado

## O que é

Página web separada do dashboard de conversas, acessível em `/pagamentos`, com login pela mesma senha do dashboard atual. Mostra todas as consultas com algum valor ainda não recebido, para que a secretária possa registrar pagamentos.

## O que aparece na lista

Uma tabela com uma linha por pendência. Um mesmo paciente pode aparecer em duas linhas se tiver tanto a taxa de reserva quanto a consulta em aberto.

Colunas:
- **Paciente** — nome do paciente
- **Médico** — Dr. Júlio ou Dra. Bruna
- **Data e hora** — da consulta
- **Tipo** — badge colorido: "Taxa de reserva" (verde) ou "Consulta" (vermelho)
- **Valor** — campo editável pré-preenchido: R$100 para taxa de reserva; valor calculado pelo perfil do paciente para consulta
- **Ação** — botão "Pago"

## O que define "pagamento em aberto"

- Taxa de reserva: consulta com `booking_fee_paid_at` nulo e `booking_fee_waived` = false
- Consulta: consulta com `paid_at` nulo
- Apenas consultas com status `scheduled` ou `completed`

## O que acontece ao clicar em Pago

A secretária edita o valor se necessário, seleciona a forma de pagamento (PIX / Cartão de crédito / Cartão de débito / Dinheiro) e clica em Pago. A linha some da tabela sem recarregar a página. No banco, o campo correto é atualizado com a data/hora atual:
- Linha de taxa → `booking_fee_paid_at`
- Linha de consulta → `paid_at`

Além disso, a planilha de pagamentos (Google Sheets) é preenchida e um e-mail de notificação é enviado para a clínica, com o mesmo formato usado pelo chatbot — mas sem link de comprovante, já que o pagamento foi registrado manualmente.

## Onde fica

Nova rota no `dashboard/main.py` existente. Mesma conexão com o banco. Template HTML próprio em `dashboard/templates/pagamentos.html`.

## Acesso

A página exige login com senha. A senha é configurada via variável de ambiente `DASHBOARD_PASSWORD` (já existente no projeto). A secretária acessa digitando a senha uma vez no navegador.
