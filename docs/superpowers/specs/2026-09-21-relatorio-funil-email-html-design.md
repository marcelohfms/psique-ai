# E-mail do relatório de funil em HTML

Data: 2026-09-21
Status: aprovado (design + mockup), aguardando plano.
Origem: follow-up do PR #249 (relatório de funil). A dona achou o e-mail em texto puro feio; mockup HTML aprovado.

## Contexto e problema

O relatório de funil (PR #249) envia o corpo em texto puro. Fica cru e difícil de ler de bater o olho. A dona aprovou um mockup em HTML com funil visual. Esta feature troca só a apresentação do e-mail.

## Objetivo

Enviar o relatório de funil como e-mail HTML (visual aprovado), mantendo uma versão em texto como fallback.

## Não-objetivos

- Não muda o cálculo do funil, a periodicidade, o cohort de 30 dias, nem o corte pendente/perdido.
- Não muda o destinatário: continua só para `FUNNEL_REPORT_EMAIL`, nunca para a clínica.
- Não altera o caminho de e-mail da clínica (`send_clinic_notification_email` fica intacto).
- Sem imagens externas, sem JS, sem CSS externo (e-mail).

## Visual aprovado (do mockup)

- Cabeçalho verde-petróleo (`#0f766e`) com "Funil de leads — Clínica Psique" e o período ("Semana de DD/MM/AAAA · últimos 30 dias").
- Conversão em destaque: número grande (ex.: 44%) + linha "22 de 50 leads viraram consulta paga".
- Funil em barras horizontais que encurtam de Interessados → Qualificados → Agendados, com a largura proporcional ao total; as quedas aparecem em vermelho ("↓ 22 desistiram no cadastro", "↓ 6 viram horário e não pagaram").
- Dois cartões: Pendentes (quentes, laranja) e Perdidos (frios, cinza).
- Tabela de pendentes: Paciente, WhatsApp, Etapa (etapa como "pill" colorida). Mostra o telefone real (o e-mail vai só para a dona; o mockup mascarou só por ser exemplo).
- Rodapé: "Relatório automático · enviado só para você".

Requisitos de e-mail: layout em tabelas, estilos inline, cores em hex. É o que o mockup já segue.

## Componentes

- **Modificar** `app/funnel_report.py`: adicionar `build_html_report(result, now, tz) -> str` (puro, monta o HTML com estilos inline). `format_report` (texto) permanece e vira o fallback em texto.
- **Modificar** `app/email_sender.py`:
  - `_send_email(...)` ganha um parâmetro opcional `html_body: str | None = None`; quando presente, anexa DUAS partes no `multipart/alternative` (texto primeiro, HTML depois — o cliente escolhe). Sem `html_body`, comportamento idêntico ao de hoje (só texto).
  - `send_report_email(subject, body, to_email, html_body=None)` passa o `html_body` adiante.
- **Modificar** `scripts/send_funnel_report.py`: gerar `body` (texto) e `html` (`build_html_report`), e chamar `send_report_email(subject, body, to_email, html_body=html)`. O `print` de log continua só com o agregado (sem PII).
- **Testes:** `tests/test_funnel_report.py` (novos casos de `build_html_report`), `tests/test_funnel_report_cron.py` (o cron passa `html_body`; `_send_email` com/sem html).

## Cálculo das larguras das barras (no build_html_report)

- Interessados = 100% (base). Qualificados = round(100 * qualificados / interessados)%. Agendados = round(100 * agendados / interessados)%. Se interessados = 0, todas as barras ficam em 0% e o texto de queda é omitido.
- Larguras têm um mínimo visual (ex.: 6%) para uma barra com valor > 0 nunca sumir.

## Salvaguardas

- Aditivo: `_send_email` só ganha um parâmetro opcional; o caminho da clínica não muda (não passa `html_body`).
- Fallback em texto sempre presente (acessibilidade e clientes sem HTML).
- Nada de I/O novo; a lógica de HTML é pura e testável.
- Privacidade inalterada: destinatário só `FUNNEL_REPORT_EMAIL`; sem PII no log.

## Riscos e mitigações

- **Renderização entre clientes de e-mail:** mitigada usando tabelas + estilos inline (o mockup já segue). Gmail é o alvo principal.
- **Quebra do caminho da clínica:** mitigada mantendo `html_body` opcional e `send_clinic_notification_email` intacto (teste garante que segue só texto).

## Fora de escopo

- Logo da clínica no cabeçalho (pode entrar depois; exigiria imagem embarcada).
- Gráficos por médico ou por origem.
