#!/usr/bin/env python3
"""Populate messages table from existing LangGraph checkpoints.

Reads every thread from the Postgres checkpoint table, reconstructs the
conversation history via aget_state_history(), and inserts each message into
the `messages` Supabase table preserving the original timestamps.

Already-populated phones are skipped so the script is safe to re-run.

Usage (from project root):
    uv run python scripts/backfill_messages.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Allow importing app modules from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

import psycopg
from psycopg.rows import dict_row
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.database import get_supabase
from app.graph.graph import build_graph


def _strip_phone(phone: str) -> str:
    return phone.replace("@s.whatsapp.net", "")


async def main() -> None:
    conn_string = os.environ["SUPABASE_CONNECTION_STRING"]

    # ── Step 1: collect thread IDs ────────────────────────────────────────────
    print("Fetching thread IDs from checkpoints…")
    async with await psycopg.AsyncConnection.connect(
        conn_string,
        autocommit=True,
        prepare_threshold=None,
        row_factory=dict_row,
    ) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        )
        thread_ids = [row["thread_id"] async for row in cur]

    print(f"Found {len(thread_ids)} thread(s)\n")
    if not thread_ids:
        return

    # ── Step 2: for each thread, extract and insert messages ──────────────────
    async with await psycopg.AsyncConnection.connect(
        conn_string,
        autocommit=True,
        prepare_threshold=None,
        row_factory=dict_row,
    ) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)
        supabase = await get_supabase()

        total = 0
        for thread_id in thread_ids:
            phone = _strip_phone(thread_id)
            config = {"configurable": {"thread_id": thread_id}}

            try:
                # Skip phones that already have rows in messages
                existing = (
                    await supabase.from_("messages")
                    .select("id")
                    .eq("phone", phone)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    print(f"  {phone}: already populated — skipping")
                    continue

                # aget_state_history yields snapshots newest→oldest; reverse for
                # chronological order so we can diff message counts correctly.
                history = []
                async for snapshot in graph.aget_state_history(config):
                    history.append(snapshot)
                history.reverse()

                if not history:
                    print(f"  {phone}: no history found")
                    continue

                rows: list[dict] = []
                prev_count = 0

                for snapshot in history:
                    msgs = (snapshot.values or {}).get("messages", [])
                    # created_at is an ISO string set by LangGraph when the
                    # checkpoint was written — close enough for our purposes.
                    ts: str | None = snapshot.created_at

                    new_msgs = msgs[prev_count:]
                    prev_count = len(msgs)

                    for msg in new_msgs:
                        row: dict | None = None

                        if isinstance(msg, HumanMessage):
                            content = (
                                msg.content
                                if isinstance(msg.content, str)
                                else str(msg.content)
                            )
                            if content.strip():
                                row = {"phone": phone, "role": "user", "content": content}

                        elif isinstance(msg, AIMessage) and not msg.tool_calls:
                            content = (
                                msg.content if isinstance(msg.content, str) else ""
                            )
                            if content.strip():
                                row = {
                                    "phone": phone,
                                    "role": "assistant",
                                    "content": content,
                                }

                        if row:
                            if ts:
                                row["created_at"] = ts
                            rows.append(row)

                if not rows:
                    print(f"  {phone}: 0 messages extracted")
                    continue

                # Insert in batches of 100 to stay within Supabase limits
                batch_size = 100
                for i in range(0, len(rows), batch_size):
                    await supabase.from_("messages").insert(
                        rows[i : i + batch_size]
                    ).execute()

                print(f"  {phone}: inserted {len(rows)} messages")
                total += len(rows)

            except Exception as exc:
                print(f"  {phone}: ERROR — {exc}")

    print(f"\nDone. Total messages inserted: {total}")


if __name__ == "__main__":
    asyncio.run(main())
