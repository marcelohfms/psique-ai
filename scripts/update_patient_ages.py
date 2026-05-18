"""
Monthly script to recalculate and update patient ages from birth_date.

Queries every user row that has a birth_date, computes the current age,
and writes it back only when it differs from the stored value.

Run via: uv run python scripts/update_patient_ages.py
Scheduled: 1st of each month at 03:00 UTC via GitHub Actions.
"""
import asyncio
import os
from datetime import datetime, date

from dotenv import load_dotenv
load_dotenv()


def _age_from_birth_date(birth_date_str: str, today: date | None = None) -> int | None:
    """Return age in full years given a birth date string.

    Accepts dd/mm/yyyy (stored format) and yyyy-mm-dd (ISO).
    Returns None if the string cannot be parsed.
    """
    if not birth_date_str:
        return None

    today = today or date.today()
    s = birth_date_str.strip()

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%Y/%m/%d"):
        try:
            bd = datetime.strptime(s, fmt).date()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            return age
        except ValueError:
            continue

    return None


async def main() -> None:
    from supabase import acreate_client

    client = await acreate_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_KEY"],
    )

    # Fetch every user that has a birth_date
    result = await (
        client.from_("users")
        .select("id, name, patient_name, birth_date, age")
        .not_.is_("birth_date", "null")
        .execute()
    )

    rows = result.data or []
    today = date.today()
    print(f"Found {len(rows)} user(s) with birth_date (today: {today})")

    updated = 0
    skipped = 0
    errors = 0

    for row in rows:
        user_id = row["id"]
        birth_date_str = row.get("birth_date") or ""
        stored_age = row.get("age")

        correct_age = _age_from_birth_date(birth_date_str, today)

        if correct_age is None:
            print(f"  [WARN] Could not parse birth_date={birth_date_str!r} for user {user_id}")
            errors += 1
            continue

        if correct_age == stored_age:
            skipped += 1
            continue

        # Age changed — update the record
        display_name = row.get("patient_name") or row.get("name") or user_id
        try:
            await client.from_("users").update({"age": correct_age}).eq("id", user_id).execute()
            print(f"  Updated {display_name}: {stored_age} → {correct_age}")
            updated += 1
        except Exception as e:
            print(f"  [ERROR] Failed to update {user_id}: {e}")
            errors += 1

    print(f"\nDone — updated: {updated}, unchanged: {skipped}, errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
