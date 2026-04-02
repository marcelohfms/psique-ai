"""
Import patients from Numbers spreadsheet into Supabase users table.
Upserts by phone number (updates existing, inserts new).

File columns:
  0: Nome
  1: Nome da Mãe
  2: Data Nascimento  (dd/mm/yyyy)
  3: Idade            (ex: "15 anos e 10 meses")
  4: Cpf
  5: E-mail
  6: Encaminhado(a) por
  7: Prof. Responsável
  8: Celular

All patients in this report belong to Dr. Júlio.

Usage:
    python3 scripts/import_patients_numbers.py [--dry-run]
"""
import asyncio
import os
import re
import sys
from dotenv import load_dotenv
load_dotenv()

NUMBERS_FILE = (
    "/Users/ayexatavares/Library/Mobile Documents/"
    "com~apple~Numbers/Documents/"
    "Júlio Cesar Marques Gouveia de Melo - 04-03-2026 - Relatorio de Pacientes.numbers"
)

JULIO_ID = "d5baa58b-a788-4f40-b8c0-512c189150be"
BRUNA_ID = "18b01f87-eacd-4905-bd4a-a8293991e6fd"

DOCTOR_MAP = {
    "bruna rafaela evangelista de lima": BRUNA_ID,
    "júlio cesar marques gouveia de melo": JULIO_ID,
    "julio cesar marques gouveia de melo": JULIO_ID,
}


def clean_phone(raw):
    if not raw:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 8:
        return None
    if not digits.startswith("55"):
        digits = "55" + digits
    # 12 digits = missing the 9th digit → insert 9 after country code + DDD (position 4)
    if len(digits) == 12:
        digits = digits[:4] + "9" + digits[4:]
    return digits if len(digits) == 13 else None


def parse_age(raw):
    if not raw or str(raw).strip() in ("-", "None", ""):
        return None
    m = re.search(r"(\d+)\s*ano", str(raw))
    return int(m.group(1)) if m else None


def cell(row, idx):
    try:
        v = row[idx].value
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


def parse_rows(rows):
    skipped_no_phone = 0
    skipped_no_name = 0
    records = {}  # phone → record (dedup by phone, last wins)

    for row in rows[1:]:  # skip header
        nome = cell(row, 0)
        if not nome:
            skipped_no_name += 1
            continue

        raw_phone = cell(row, 8)
        phone = clean_phone(raw_phone)
        if not phone:
            skipped_no_phone += 1
            continue

        birth_date = cell(row, 2) or None
        age = parse_age(cell(row, 3))
        email = cell(row, 5).lower() or None
        referral = cell(row, 6) or None
        prof_responsavel = cell(row, 7).strip().lower()
        doctor_id = DOCTOR_MAP.get(prof_responsavel)  # None if unknown

        nome = nome.title()
        record = {
            "number": phone,
            "name": nome,
            "patient_name": nome,
            "birth_date": birth_date,
            "age": age,
            "email": email if email else None,
            "is_patient": True,
            "active": True,
            "referral_professional": referral if referral else None,
            "doctor_id": doctor_id,
        }
        records[phone] = record

    print(f"  Skipped (no name):  {skipped_no_name}")
    print(f"  Skipped (no phone): {skipped_no_phone}")
    return list(records.values())


async def main():
    dry_run = "--dry-run" in sys.argv

    from numbers_parser import Document
    from supabase import acreate_client

    print("Reading Numbers file...")
    doc = Document(NUMBERS_FILE)
    table = doc.sheets[0].tables[0]
    rows = list(table.iter_rows())
    print(f"  Total rows (incl. header): {len(rows)}")

    records = parse_rows(rows)
    print(f"  Valid patients to import: {len(records)}")

    if dry_run:
        print("\n--- DRY RUN (first 5 records) ---")
        for r in records[:5]:
            print(r)
        return

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    batch_size = 50
    inserted = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        await client.from_("users").upsert(batch, on_conflict="number").execute()
        inserted += len(batch)
        print(f"  {inserted}/{len(records)} upserted...")

    print(f"\nDone! {len(records)} patients imported/updated.")


if __name__ == "__main__":
    asyncio.run(main())
