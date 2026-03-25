"""
AI-vs-AI conversation simulator for Psique Chatbot.

Runs each scenario in scenarios.py by alternating between:
  - Patient LLM (gpt-4.1-mini): plays the patient persona
  - Eva (real LangGraph graph): processes messages through the full pipeline

Results are printed live and saved to scripts/scenario_results.md.

Usage:
    uv run python scripts/run_scenarios.py
    uv run python scripts/run_scenarios.py --scenario 1   # run only scenario index 1
"""
import asyncio
import argparse
import os
import sys
from datetime import datetime
from unittest.mock import patch, AsyncMock
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI

TZ = ZoneInfo("America/Recife")
MAX_TURNS = 25


async def reset_state(phone: str, supabase_client) -> None:
    """Wipe all conversation state for a test phone."""
    from app.database import _strip_phone
    stripped = _strip_phone(phone)
    try:
        await supabase_client.from_("messages").delete().eq("phone", stripped).execute()
    except Exception:
        pass
    try:
        await supabase_client.from_("users").delete().eq("number", stripped).execute()
    except Exception:
        pass
    for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
        try:
            await supabase_client.from_(table).delete().eq("thread_id", phone).execute()
        except Exception:
            pass


async def patient_reply(
    openai_client: AsyncOpenAI,
    persona: str,
    history: list[tuple[str, str]],
    eva_message: str,
) -> str:
    """Generate the patient's next message using gpt-4.1-mini."""
    system = (
        persona.strip()
        + "\n\n"
        "Você está conversando com Eva, a assistente virtual da Clínica Psique. "
        "Responda APENAS como o paciente descrito acima — de forma natural, breve e em português. "
        "Quando seu objetivo for completamente atingido, responda exatamente: [FIM]"
    )
    messages = [{"role": "system", "content": system}]
    for speaker, text in history:
        role = "assistant" if speaker == "patient" else "user"
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": eva_message})

    resp = await openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=messages,
        max_tokens=300,
        temperature=0.8,
    )
    return resp.choices[0].message.content.strip()


async def run_scenario(scenario: dict, openai_client: AsyncOpenAI, supabase_client) -> tuple[list, str]:
    """
    Run one scenario. Returns (turns, status) where:
      turns = list of (speaker_label, message)
      status = "done" | "timeout" | "error"
    """
    from app.main import process_message

    phone = scenario["phone"] + "@s.whatsapp.net"
    patient_label = scenario.get("patient_label", "Paciente")

    await reset_state(phone, supabase_client)

    turns: list[tuple[str, str]] = []
    status = "timeout"

    patient_msg = scenario["opening"]

    for turn_num in range(MAX_TURNS):
        # ── Patient speaks ────────────────────────────────────────────────────
        turns.append(("patient", patient_msg))
        print(f"  [{patient_label}]: {patient_msg}")

        # ── Eva responds (capture send_text, suppress save_message) ──────────
        eva_replies: list[str] = []

        async def _capture_send(to_phone: str, text: str) -> None:
            eva_replies.append(text)

        try:
            with patch("app.graph.nodes.send_text", side_effect=_capture_send), \
                 patch("app.graph.tools.send_text", new_callable=AsyncMock), \
                 patch("app.database.save_message", new_callable=AsyncMock):
                await process_message(phone, patient_msg)
        except Exception as e:
            print(f"  [ERRO]: {e}")
            status = "error"
            break

        eva_message = "\n".join(eva_replies) if eva_replies else "(sem resposta)"
        turns.append(("eva", eva_message))
        print(f"  [Eva]: {eva_message}\n")

        # ── Check for conversation end ────────────────────────────────────────
        if not eva_replies:
            break

        # ── Patient LLM generates next reply ─────────────────────────────────
        history_for_llm = [(spk, msg) for spk, msg in turns]
        patient_msg = await patient_reply(openai_client, scenario["persona"], history_for_llm[:-1], eva_message)

        if "[FIM]" in patient_msg:
            clean = patient_msg.replace("[FIM]", "").strip()
            if clean:
                turns.append(("patient", clean))
                print(f"  [{patient_label}]: {clean}")
            status = "done"
            break

    return turns, status


def build_markdown(results: list[tuple[dict, list, str]], generated_at: str) -> str:
    lines = [f"# Scenario Results — {generated_at}\n"]

    status_emoji = {"done": "✅", "timeout": "⏱️", "error": "❌"}

    for i, (scenario, turns, status) in enumerate(results, 1):
        emoji = status_emoji.get(status, "❓")
        n_turns = len([t for t in turns if t[0] == "patient"])
        lines.append(f"---\n\n## {i}. {scenario['name']}  {emoji} ({n_turns} turnos do paciente)\n")
        lines.append(f"**Telefone de teste**: {scenario['phone']}\n")

        patient_label = scenario.get("patient_label", "Paciente")
        for speaker, text in turns:
            label = patient_label if speaker == "patient" else "Eva"
            lines.append(f"**[{label}]**: {text}\n")

        lines.append("")

    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="Run AI-vs-AI conversation scenarios")
    parser.add_argument("--scenario", type=int, default=None, help="Run only this scenario index (1-based)")
    args = parser.parse_args()

    from scripts.scenarios import SCENARIOS
    from supabase import acreate_client
    from app.graph.graph import build_graph
    from app.graph import graph as graph_module

    # Init Supabase
    supabase_client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    # Init LangGraph with Supabase checkpointer if available, else in-memory
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    checkpointer_ctx = None
    if conn_string:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        checkpointer_ctx = AsyncPostgresSaver.from_conn_string(conn_string)
        graph_module.chatbot = build_graph(await checkpointer_ctx.__aenter__())
        print("Using Supabase checkpointer.\n")
    else:
        graph_module.chatbot = build_graph()
        print("Using in-memory checkpointer (SUPABASE_CONNECTION_STRING not set).\n")

    openai_client = AsyncOpenAI()

    scenarios = SCENARIOS
    if args.scenario is not None:
        scenarios = [SCENARIOS[args.scenario - 1]]

    results = []
    generated_at = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")

    try:
        for i, scenario in enumerate(scenarios, 1):
            print(f"\n{'='*60}")
            print(f"Scenario {i}/{len(scenarios)}: {scenario['name']}")
            print(f"{'='*60}\n")
            turns, status = await run_scenario(scenario, openai_client, supabase_client)
            results.append((scenario, turns, status))
            status_label = {"done": "✅ concluído", "timeout": "⏱️ timeout", "error": "❌ erro"}.get(status, status)
            print(f"\n→ {status_label}")

    finally:
        if checkpointer_ctx:
            await checkpointer_ctx.__aexit__(None, None, None)

    # Write results
    report_path = os.path.join(os.path.dirname(__file__), "scenario_results.md")
    md = build_markdown(results, generated_at)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"\n\n{'='*60}")
    print(f"Report saved to: {report_path}")
    done = sum(1 for _, _, s in results if s == "done")
    print(f"Summary: {done}/{len(results)} scenarios completed successfully.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
