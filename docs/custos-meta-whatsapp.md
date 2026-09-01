# Custos da Meta (WhatsApp Business API)

Registro mensal do consumo e da cobrança aproximada da Meta pelo envio de
mensagens via WhatsApp Business API, extraído do painel de insights da Meta
(WhatsApp Manager). Serve de linha de base para medir o impacto de mudanças
de cobrança da Meta a partir de outubro de 2026.

> Observação da própria Meta: todos os dados de insights são aproximados e
> podem diferir do valor cobrado na fatura, devido a pequenas variações no
> processamento de dados.

## Junho/2026

| Categoria | Mensagens entregues | Mensagens pagas | Cobrança |
|---|---|---|---|
| Marketing | 41 | 41 | $2,56 USD |
| Utilidade | 340 | 247 | $1,70 USD |
| Autenticação | 0 | 0 | $0,00 USD |
| Autenticação (internacional) | 0 | 0 | $0,00 USD |
| Fornecedor de IA | 0 | 0 | $0,00 USD |
| Serviço | 4.939 | — | — |
| **Total entregues** | **5.320** | **288** | **$4,26 USD** |

Mensagens enviadas: 5.319 · recebidas: 3.386 · gratuitas entregues: 5.032
(atendimento ao cliente grátis).

## Julho/2026

| Categoria | Mensagens entregues | Mensagens pagas | Cobrança |
|---|---|---|---|
| Marketing | 88 | 88 | $5,50 USD |
| Utilidade | 310 | 204 | $1,41 USD |
| Autenticação | 0 | 0 | $0,00 USD |
| Autenticação (internacional) | 0 | 0 | $0,00 USD |
| Fornecedor de IA | 0 | 0 | $0,00 USD |
| Serviço | 4.582 | — | — |
| **Total entregues** | **4.980** | **292** | **$6,91 USD** |

Mensagens enviadas: 4.982 · recebidas: 4.290 · gratuitas entregues: 4.688
(atendimento ao cliente grátis).

## Agosto/2026 (parcial, até 26/08)

| Categoria | Mensagens entregues | Mensagens pagas | Cobrança |
|---|---|---|---|
| Marketing | 64 | 64 | $4,00 USD |
| Utilidade | 181 | 135 | $0,94 USD |
| Autenticação | 0 | 0 | $0,00 USD |
| Autenticação (internacional) | 0 | 0 | $0,00 USD |
| Fornecedor de IA | 0 | 0 | $0,00 USD |
| Serviço | 3.037 | — | — |
| **Total entregues** | **3.282** | **199** | **$4,94 USD** |

Mensagens enviadas: 3.281 · recebidas: 3.392 · gratuitas entregues: 3.083
(atendimento ao cliente grátis).

> Mês em andamento na data do registro (26/08/2026), valores ainda não fechados.

## Mudança de cobrança em outubro/2026

A partir de 1º de outubro de 2026 a Meta passa a cobrar por mensagens que
hoje são gratuitas. Mensagens de serviço (respostas livres dentro da janela
de 24h após o paciente escrever) deixam de ser grátis. Mensagens de
utilidade também perdem a gratuidade quando enviadas dentro dessa mesma
janela. A cobrança passa a valer o mesmo valor por mensagem já praticado
hoje para utilidade e autenticação, por país, mas sem desconto por volume
para mensagens de serviço. A Meta ainda não publicou a tabela oficial de
taxas para essa mudança; a expectativa é que publique em 1º de setembro de
2026.

Fontes: [Pricing on the WhatsApp Business Platform](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing),
[Zendesk — Announcing upcoming changes to WhatsApp Business messaging pricing](https://support.zendesk.com/hc/en-us/articles/11113277351322-Announcing-upcoming-changes-to-WhatsApp-Business-messaging-pricing).

## Projeção de impacto (com o volume atual)

Estimativa de quanto a fatura teria sido em cada mês se a regra de outubro
já estivesse em vigor. Taxa por mensagem calculada a partir da própria
cobrança de utilidade de cada mês (cobrança de utilidade dividida pelas
mensagens pagas de utilidade), que ficou estável em torno de $0,0069 USD
por mensagem. Essa taxa foi aplicada a todas as mensagens hoje gratuitas
(serviço + utilidade dentro da janela de 24h).

| Mês | Mensagens hoje grátis | Custo adicional estimado | Cobrança real | Total projetado |
|---|---|---|---|---|
| Junho/2026 | 5.032 | $34,72 USD | $4,26 USD | ~$38,98 USD |
| Julho/2026 | 4.688 | $32,35 USD | $6,91 USD | ~$39,26 USD |
| Agosto/2026 (parcial, até 26/08) | 3.083 | $21,27 USD | $4,94 USD | ~$26,21 USD |
| Agosto/2026 (projeção mês cheio) | ~3.675 | ~$25,36 USD | ~$5,89 USD | ~$31,25 USD |

Com o volume atual, a fatura mensal deve saltar de uma faixa de $4 a $7 USD
para uma faixa de $26 a $39 USD a partir de outubro, um aumento de
aproximadamente 7 a 9 vezes. Essa é uma estimativa: a taxa real para
mensagens de serviço só será confirmada quando a Meta publicar a tabela
oficial em setembro de 2026, e pode diferir do valor de utilidade usado
aqui.

## Como atualizar

1. Abrir o painel de insights no WhatsApp Manager da Meta.
2. Copiar os números de mensagens entregues, mensagens pagas por categoria
   e a cobrança total aproximada do mês.
3. Adicionar uma nova seção acima seguindo o mesmo formato de tabela.
4. A partir de outubro/2026, comparar a cobrança total contra os meses
   anteriores para medir o impacto real da mudança de preço da Meta, e
   atualizar a taxa por mensagem usada na projeção quando a Meta publicar a
   tabela oficial em setembro/2026.
