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
| WhatsApp | UAZAPI |
| Banco de dados | Supabase (PostgreSQL) |
| Gerenciador de pacotes | uv |

## Arquitetura

```
WhatsApp (UAZAPI)
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
  └── transfer_to_human     → UAZAPI
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
| `UAZAPI_BASE_URL` | URL base da instância UAZAPI |
| `UAZAPI_TOKEN` | Token de autenticação UAZAPI |
| `OPENAI_API_KEY` | Chave da API OpenAI |
| `SUPABASE_CONNECTION_STRING` | Connection string PostgreSQL (shared pooler, porta 6543) |
| `SUPABASE_URL` | URL do projeto Supabase |
| `SUPABASE_KEY` | Chave de serviço do Supabase |

## Executar localmente

```bash
uv run uvicorn app.main:app --reload --port 8000
```

O webhook da UAZAPI deve apontar para `https://<seu-domínio>/webhook`.

## Médicos cadastrados

| Nome | Mínimo de idade |
|------|----------------|
| Dr. Júlio | 0 anos |
| Dra. Bruna | 12 anos |

## Duração das consultas

- Paciente adulto → 1 hora
- Paciente menor de 18 anos (primeira consulta) → 2 horas ou duas sessões de 1 hora (1h pais + 1h paciente)
