# Psique Chatbot

AI WhatsApp chatbot for Psique psychiatry clinic. Built with FastAPI + LangGraph + OpenAI.

## Stack
- **FastAPI** — webhook receiver
- **LangGraph** — conversation state machine
- **OpenAI** — LLM (GPT-4o)
- **UAZAPI** — WhatsApp API (`https://marceloferro.uazapi.com`)
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
