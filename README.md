# Psique AI — WhatsApp Chatbot

Chatbot de atendimento via WhatsApp para a **Clínica Psique**, especializada em psiquiatria. Construído com FastAPI, LangGraph e OpenAI.

## Funcionalidades

- **Coleta de dados** — nome, idade, paciente próprio ou terceiro, médico preferido
- **Agendamento de consultas** — integração com Google Calendar (Dr. Júlio / Dra. Bruna)
- **Solicitação de documentos** — laudo, exame, relatório, receita, declaração
- **Transferência para humano** — handoff para atendente quando necessário
- **Memória entre conversas** — usuários retornantes não precisam repetir seus dados

## Stack

| Camada | Tecnologia |
|--------|-----------|
| API | FastAPI |
| Orquestração de conversa | LangGraph |
| LLM | OpenAI GPT-4o |
| WhatsApp | Chatwoot + Meta Cloud API |
| Banco de dados | Supabase (PostgreSQL) |
| Gerenciador de pacotes | uv |

## Arquitetura

```
WhatsApp (Meta Cloud API)
      ↓
  /webhook  (FastAPI)
      ↓
  Buffer (debounce 3s)
      ↓
  LangGraph
  ├── collect_info    — coleta dados via LLM + structured output
  └── patient_agent   — agente com tools (schedule / document / handoff)
            ↓
        ToolNode
  ├── schedule_appointment  → Google Calendar
  ├── request_document      → Supabase
  └── transfer_to_human     → Chatwoot
```

**Persistência:**
- Estado da conversa → LangGraph `AsyncPostgresSaver` (Supabase)
- Dados do paciente → tabela `users` (Supabase) — reutilizados em visitas futuras

## Instalação

```bash
# Clonar e entrar no projeto
git clone https://github.com/marcelohfms/psique-ai
cd psique-ai

# Instalar dependências
uv sync

# Configurar variáveis de ambiente
cp .env.example .env
# edite o .env com suas credenciais
```

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `DEBOUNCE_SECONDS` | Tempo de debounce do buffer de mensagens (padrão: `3`) |
| `SCHEDULING_DEBOUNCE_SECONDS` | Debounce estendido durante o agendamento, logo após a Eva oferecer horários (padrão: `9`) |
| `SLOT_OFFER_DEBOUNCE_WINDOW_SECONDS` | Janela, em segundos, em que o debounce estendido vale após uma oferta de horários (padrão: `180`) |
| `WHATSAPP_TOKEN` | Token permanente da Meta Cloud API |
| `WHATSAPP_PHONE_NUMBER_ID` | ID do número de telefone na Meta Cloud API |
| `WHATSAPP_VERIFY_TOKEN` | Token de verificação do webhook da Meta |
| `CHATWOOT_BASE_URL` | URL da instância Chatwoot |
| `CHATWOOT_ACCOUNT_ID` | ID da conta no Chatwoot |
| `CHATWOOT_AGENT_BOT_TOKEN` | Token do Agent Bot no Chatwoot |
| `CHATWOOT_INBOX_ID` | ID da inbox no Chatwoot |
| `OPENAI_API_KEY` | Chave da API OpenAI |
| `SUPABASE_CONNECTION_STRING` | Connection string PostgreSQL (shared pooler, porta 6543) |
| `SUPABASE_URL` | URL do projeto Supabase |
| `SUPABASE_KEY` | Chave de serviço do Supabase |

## Executar localmente

```bash
uv run uvicorn app.main:app --reload --port 8000
```

O webhook da Meta Cloud API deve apontar para `https://<seu-domínio>/webhook`.

## Médicos cadastrados

| Nome | Mínimo de idade |
|------|----------------|
| Dr. Júlio | 0 anos |
| Dra. Bruna | 12 anos |

## Duração das consultas

- Paciente adulto → 1 hora
- Paciente menor de 18 anos (primeira consulta) → 2 horas ou duas sessões de 1 hora (1h pais + 1h paciente)

## Atualização do Chatwoot

Versão em produção: **4.19.0** (subida de 4.16.2 → 4.18.0 em 24/09/2026, e 4.18.0 → 4.19.0 em 21/10/2026). Imagem Docker `chatwoot/chatwoot:<versão>`, rodando no Easypanel (VPS, projeto `evolution`).

**Atenção: a mesma instância atende dois projetos** — conta 1 "Psique" (a Eva) e conta 2 "Dra. Ana Luísa". Atualizar, reiniciar ou restaurar afeta os dois ao mesmo tempo, e o backup do banco salva os dois juntos.

Antes de atualizar, confira nas notas de lançamento se mudaram webhooks, agent bots ou autenticação de token. A Eva depende do header `api_access_token` (ver [`app/chatwoot.py`](app/chatwoot.py)) e do webhook do agent bot entregando os eventos `message_created` e `conversation_updated` com as etiquetas.

### Passo a passo

```bash
# 1. Dump do banco ANTES (console Bash do serviço chatwoot-db no Easypanel)
pg_dump -U postgres -Fc evolution > /var/lib/postgresql/data/backup_<versão-atual>.dump

# 2. No Easypanel, troque a imagem do serviço `chatwoot` para a nova versão e
#    faça o deploy. Esse container roda as migrations, então vem primeiro.
#    Espere ficar saudável.

# 3. Troque a imagem do serviço `chatwoot-sidekiq` para a mesma versão e
#    faça o deploy.
```

Faça fora do horário de atendimento (a parada é de poucos segundos, mas derruba os dois projetos).

### Verificação pós-deploy

Health do Chatwoot em `https://evolution-chatwoot.5pqooc.easypanel.host/api` (deve mostrar a nova versão com `queue_services` e `data_services` em `ok`). Health da Eva em `https://psiqueai.ayexa.com.br/health`. Mande uma mensagem de teste **nas duas contas** e confira se respondem.

O restart interrompe o run que a Eva tiver em andamento. A primeira mensagem logo após o deploy pode chegar, ser gravada e não receber resposta, ficando muda só naquela conversa. Mandar outra mensagem destrava. Isso não é falha da integração.

### Rollback

Volte as imagens dos dois serviços para a versão anterior e restaure o dump:

```bash
pg_restore -U postgres -d evolution --clean /var/lib/postgresql/data/backup_<versão-anterior>.dump
```
