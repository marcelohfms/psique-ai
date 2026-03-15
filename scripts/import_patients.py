"""
Import existing patients from Excel spreadsheet into Supabase.

Usage:
    uv run python scripts/import_patients.py

Reads: Downloads/Júlio Cesar Marques Gouveia de Melo - 04-03-2026 - Relatorio de Pacientes (1).xlsx
Writes: Supabase users table (upsert on phone number)
"""
import asyncio
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import openpyxl

DOCTOR_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
XLSX_PATH = Path.home() / "Downloads" / "Júlio Cesar Marques Gouveia de Melo - 04-03-2026 - Relatorio de Pacientes (1).xlsx"
BATCH_SIZE = 100


def clean_phone(raw: str) -> str | None:
    """Normalize phone to 55XXXXXXXXXXX format."""
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    if not digits.startswith("55"):
        digits = "55" + digits
    # Brazil: 55 + DDD (2) + number (8 or 9) = 12 or 13 digits
    if len(digits) < 12 or len(digits) > 13:
        return None
    return digits


def parse_age(raw) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("-", "", "None"):
        return None
    # "15 anos e 10 meses" → 15
    m = re.match(r"(\d+)\s+anos?", s)
    if m:
        return int(m.group(1))
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def load_rows() -> list[dict]:
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True, data_only=True)
    ws = wb.active

    # Skip first 16 rows (filter header block); row 17 has column headers
    rows_iter = ws.iter_rows(values_only=True)
    for _ in range(16):
        next(rows_iter)
    headers = [str(h).strip().lower() if h else "" for h in next(rows_iter)]
    print(f"Headers: {headers[:12]}")

    # Fixed column positions (verified from file inspection)
    idx_name = 1   # Nome
    idx_phone = 9  # Celular
    idx_age = 4    # Idade (text like "15 anos e 10 meses")
    idx_birth = 3  # Data Nascimento
    idx_email = 8  # E-mail

    print(f"Columns → name={idx_name}, phone={idx_phone}, age={idx_age}")

    seen_phones: dict[str, dict] = {}
    skipped = 0

    for row in rows_iter:
        raw_phone = row[idx_phone] if idx_phone < len(row) else None
        if not raw_phone:
            skipped += 1
            continue

        phone = clean_phone(str(raw_phone))
        if not phone:
            skipped += 1
            continue

        name = str(row[idx_name]).strip().title() if row[idx_name] else ""
        if not name:
            skipped += 1
            continue

        age = parse_age(row[idx_age] if idx_age < len(row) else None)
        raw_email = row[idx_email] if idx_email < len(row) else None
        email = str(raw_email).strip().lower() if raw_email else None

        record: dict = {
            "number": phone,
            "name": name,
            "patient_name": name,
            "age": age,
            "is_patient": True,
            "doctor_id": DOCTOR_ID,
            "active": True,
        }
        if email:
            record["email"] = email

        # Deduplicate: last occurrence wins
        seen_phones[phone] = record

    wb.close()
    print(f"Skipped rows (no phone/name): {skipped}")
    print(f"Unique phones: {len(seen_phones)}")
    return list(seen_phones.values())


async def upsert_batch(client, batch: list[dict], retries: int = 3) -> int:
    for attempt in range(retries):
        try:
            result = await (
                client.from_("users")
                .upsert(batch, on_conflict="number")
                .execute()
            )
            return len(result.data) if result.data else 0
        except Exception as e:
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"  Retrying in {wait}s after error: {e}")
                await asyncio.sleep(wait)
            else:
                raise


async def main():
    from supabase._async.client import AsyncClient, create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set in .env")
        sys.exit(1)

    rows = load_rows()
    if not rows:
        print("No rows to import.")
        return

    print(f"\nImporting {len(rows)} patients in batches of {BATCH_SIZE}...")
    client: AsyncClient = await create_client(url, key)

    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        count = await upsert_batch(client, batch)
        total += count
        print(f"  Batch {i // BATCH_SIZE + 1}: {count} rows upserted (total: {total})")

    print(f"\nDone. {total} patients imported.")


if __name__ == "__main__":
    asyncio.run(main())
