---
name: doctor-availability-format
description: Use sempre que o usuário pedir a disponibilidade / horários livres de um médico (Dr. Júlio / Dra. Bruna) — "me passe as disponibilidades", "quais horários o Dr. Júlio tem", "disponibilidade das segundas em julho", etc. Cobre como buscar os slots reais no Google Calendar e como formatar a resposta como mensagem pronta para o paciente.
---

# Disponibilidade de médico → mensagem para o paciente

Quando o usuário pede disponibilidade de um médico, faça **duas coisas**: (1) buscar os horários reais no calendário; (2) apresentar no formato de mensagem para paciente abaixo.

## Passo 1 — buscar os slots reais

Nunca invente horários. Rode, **a partir da raiz do projeto** (carrega o `.env`):

```bash
uv run python .claude/skills/doctor-availability-format/fetch_slots.py \
    --doctor julio --dates 20/07 27/07
```

- `--doctor`: `julio` ou `bruna`
- `--dates`: uma ou mais datas `DD/MM` (ex.: as segundas do mês)
- Opcionais: `--shift` (`manha`/`tarde`/`noite`/`qualquer`, default `qualquer`), `--minutes` (default 60)

O script usa `get_available_slots` de [app/google_calendar.py](../../../app/google_calendar.py), então já respeita a grade do médico, `SCHEDULE_EXCEPTIONS`, eventos ocupados e a antecedência mínima (não mostra horários no passado nem nas próximas ~4h).

Para descobrir quais datas correspondem a "as segundas de julho" etc., calcule os dias da semana no mês. Datas já passadas e dias sem vaga simplesmente não aparecem na mensagem final.

## Passo 2 — formatar como mensagem para o paciente (padrão: horários em linha)

Só inclua datas que têm horários livres. Este é o formato **padrão**: uma linha por data com os horários em sequência, sem bullets.

```
Olá! 😊 Seguem os horários disponíveis com o **Dr. Júlio** nas segundas-feiras de julho:

📅 **Segunda, 20/07**: 14h, 17h
📅 **Segunda, 27/07**: 9h, 10h, 11h, 14h, 15h, 16h, 17h

Cada consulta tem duração de 1 hora.

⚠️ Lembrando que nossos agendamentos acontecem simultaneamente, então não conseguimos garantir a disponibilidade por muito tempo — só teremos certeza quando o horário for efetivamente agendado.

Qual horário fica melhor para você? 💙
```

Regras do formato:
- Horas escritas como `9h`, `14h` (não `09:00`).
- Uma linha `📅 **<Dia>, DD/MM**: hora1, hora2, ...` por data.
- Sempre incluir a nota de duração da consulta (1h para adulto; menor de 18 na 1ª consulta ocupa 2h — ver CLAUDE.md).
- **Sempre** incluir o aviso ⚠️ de agendamentos simultâneos / sem garantia até confirmar (ver memória `feedback_availability_disclaimer`).
- Terminar com uma pergunta convidando o paciente a escolher.

### Alternativa — mesma mensagem com bullets

Se o usuário pedir explicitamente com bullets (uma linha por horário), mantenha tudo do formato padrão e troque só o bloco de datas: uma linha `📅 **<Dia>, DD/MM**` seguida de bullets `•` para cada horário.

```
📅 **Segunda, 27/07**
• 9h
• 10h
• 11h
```

## Formato resumido (uso interno, não para o paciente)

Se o usuário pedir a lista "de forma mais resumida" (uso interno, não é a mensagem para o paciente), use uma linha por data, sem saudação, sem bullets e sem o aviso ⚠️:

```
03/08 (seg): 9h
05/08 (qua): 10h, 11h
06/08 (qui): 9h, 10h, 11h, 14h, 15h
10/08 (seg): 9h, 11h, 14h, 15h
12/08 (qua): 9h, 10h, 11h
```

Regras:
- `DD/MM (dia-da-semana abreviado): hora1, hora2, ...` — um por linha.
- Dia da semana abreviado em minúsculas: seg, ter, qua, qui, sex, sáb, dom.
- Datas sem vaga não entram na lista (mesmo critério do Passo 2).
- Sem saudação, sem markdown de negrito, sem emoji, sem aviso de disponibilidade — é só a lista compacta.

## Horários online

Quando um horário sai marcado como `[online]` no `fetch_slots.py`, anote `(online)` ao lado dele na mensagem (vale para qualquer formato). Horários sem marca são presenciais e não precisam de nota.

Exemplo no formato padrão:

```
📅 **Quinta, 04/09**: 8h, 9h, 10h, 11h, 13h (online), 14h (online), 15h (online)
```

## IDs / nomes dos médicos

- `julio` → Dr. Júlio (`dr.juliogouveia@gmail.com`)
- `bruna` → Dra. Bruna (`brunalima.psiquiatra@gmail.com`)
