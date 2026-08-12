# Psique Chatbot

AI WhatsApp chatbot for Psique psychiatry clinic. Built with FastAPI + LangGraph + OpenAI.

## Stack
- **FastAPI** — webhook receiver
- **LangGraph** — conversation state machine
- **OpenAI** — LLM (GPT-4o)
- **Meta WhatsApp API** — envio via Chatwoot, recebimento via webhook Meta Cloud API
- **Google Calendar** — appointment scheduling

## Run locally
```bash
uv run uvicorn app.main:app --reload --port 8000
```

## Environment
Copy `.env.example` to `.env` and fill in the values.

## Conversation flow
1. New user → ask name
2. Ask: consultation for self or someone else?
3. Ask: already a patient?
   - Yes → ask preferred doctor (Dr. Júlio / Dra. Bruna)
   - No → explain clinic → proceed to scheduling
4. Scheduling → ask preferred day/shift → show available slots → confirm
5. At any point: document request or human handoff

## Appointment duration
- Patient < 18 years old: first consultation = 2h (or two 1h slots: 1h for parents + 1h for patient)
- Adult: 1h slot

## Worktrees (isolamento da main)

**Toda nova feature/fix nasce numa git worktree própria — nunca trabalhe direto na `main`.**
Sessões concorrentes que editam o mesmo checkout misturam trabalho não-commitado e
mexem no HEAD uma da outra. Worktrees dão isolamento físico.

- Diretório: `.worktrees/<nome-do-branch>` (já ignorado no `.gitignore`).
- Criar: `git worktree add .worktrees/<branch> -b <branch> main`
- Trabalhe, teste (`uv run pytest`) e commite **dentro** da worktree; abra o PR de lá.
- Ao terminar e mergear: `git worktree remove .worktrees/<branch>`.
- `git worktree list` mostra as ativas; `git worktree prune` limpa as órfãs.

## Tests

Run: `uv run pytest --tb=short`

**When implementing any new feature or modifying existing behavior, you MUST:**
1. Check whether existing tests in `tests/` cover the affected code paths.
2. Update tests that break due to the change.
3. Add new tests for the new behavior — unit tests for pure logic, mocked integration tests for code that touches Supabase / Google Calendar / Meta WhatsApp API / OpenAI.
4. Keep the same structure: one file per layer (`test_webhook`, `test_process_message`, `test_tools`, `test_calendar`, `test_buffer`). Add a new file only when introducing an entirely new module.

All tests must pass on CI (`.github/workflows/test.yml`) before merging to `main`.
