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

## Passo 2 — formatar como mensagem para o paciente

Só inclua datas que têm horários livres. Formato:

```
Olá! 😊 Seguem os horários disponíveis com o **Dr. Júlio** nas segundas-feiras de julho:

📅 **Segunda, 20/07**
• 14h
• 17h

📅 **Segunda, 27/07**
• 9h
• 10h
• 11h
• 14h
• 15h
• 16h
• 17h

Cada consulta tem duração de 1 hora.

⚠️ Lembrando que nossos agendamentos acontecem simultaneamente, então não conseguimos garantir a disponibilidade por muito tempo — só teremos certeza quando o horário for efetivamente agendado.

Qual horário fica melhor para você? 💙
```

Regras do formato:
- Horas escritas como `9h`, `14h` (não `09:00`).
- Uma linha `📅 **<Dia>, DD/MM**` por data, com bullets `•` para cada horário.
- Sempre incluir a nota de duração da consulta (1h para adulto; menor de 18 na 1ª consulta ocupa 2h — ver CLAUDE.md).
- **Sempre** incluir o aviso ⚠️ de agendamentos simultâneos / sem garantia até confirmar (ver memória `feedback_availability_disclaimer`).
- Terminar com uma pergunta convidando o paciente a escolher.

## IDs / nomes dos médicos

- `julio` → Dr. Júlio (`dr.juliogouveia@gmail.com`)
- `bruna` → Dra. Bruna (`brunalima.psiquiatra@gmail.com`)
