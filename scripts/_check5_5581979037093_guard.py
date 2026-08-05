"""Prova empírica: com o guard novo, a reativação por label em 05/08 12:18:27
não teria reprocessado 'Presencial'. Somente leitura."""
import asyncio
from dotenv import load_dotenv
load_dotenv()

async def main():
    from app.chatwoot import get_last_patient_message
    last = await get_last_patient_message(370)
    note_at = last.get("last_note_at")
    print("last patient message:", repr(last["content"]), "created_at:", last["created_at"])
    print("last attendant note at:", note_at)
    print("REPLAY SUPRIMIDO?" , bool(note_at and note_at >= (last.get("created_at") or 0)))

asyncio.run(main())
