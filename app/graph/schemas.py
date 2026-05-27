import re
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal

# Formats the LLM commonly produces; we normalise all of them to dd/mm/yyyy.
_BIRTH_DATE_FORMATS = [
    "%d/%m/%Y",   # 15/01/1985 — correct
    "%d/%m/%y",   # 15/01/85 — 2-digit year
    "%Y-%m-%d",   # 1985-01-15 — ISO 8601
    "%d-%m-%Y",   # 15-01-1985
    "%d-%m-%y",   # 15-01-85
    "%d.%m.%Y",   # 15.01.1985
    "%d.%m.%y",   # 15.01.85
    "%d %m %Y",   # 15 01 1985
    "%d %m %y",   # 15 01 85
    "%m/%d/%Y",   # US format (last resort)
]

# Separators accepted in the flexible regex path
_SEP = r"[/\-. ]"


def _year2to4(yy: int) -> int:
    """Convert 2-digit year to 4-digit. <40 → 2000s, >=40 → 1900s."""
    return (2000 + yy) if yy < 40 else (1900 + yy)


def _parse_birth_date(value: str) -> str | None:
    """Try to parse *value* in many common formats and return dd/mm/yyyy.
    Returns None only if the value is genuinely unparseable."""
    s = value.strip()

    # 1. Try all strptime formats first (most reliable)
    for fmt in _BIRTH_DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            # Fix 2-digit years that strptime maps to 1969-2068
            if dt.year < 100:
                dt = dt.replace(year=_year2to4(dt.year % 100))
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            continue

    # 2. Flexible separator: dd[sep]mm[sep]yyyy or dd[sep]mm[sep]yy
    m = re.fullmatch(r"(\d{1,2})" + _SEP + r"(\d{1,2})" + _SEP + r"(\d{2,4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if len(m.group(3)) == 2:
            y = _year2to4(y)
        try:
            return datetime(y, mo, d).strftime("%d/%m/%Y")
        except ValueError:
            pass

    # 3. No separator — 8 digits: try ddmmyyyy then yyyymmdd
    m8 = re.fullmatch(r"(\d{8})", s)
    if m8:
        digits = s
        # ddmmyyyy
        try:
            return datetime(int(digits[4:]), int(digits[2:4]), int(digits[:2])).strftime("%d/%m/%Y")
        except ValueError:
            pass
        # yyyymmdd
        try:
            return datetime(int(digits[:4]), int(digits[4:6]), int(digits[6:])).strftime("%d/%m/%Y")
        except ValueError:
            pass

    # 4. No separator — 6 digits: ddmmyy
    m6 = re.fullmatch(r"(\d{6})", s)
    if m6:
        digits = s
        y = _year2to4(int(digits[4:]))
        try:
            return datetime(y, int(digits[2:4]), int(digits[:2])).strftime("%d/%m/%Y")
        except ValueError:
            pass

    return None


class CollectInfoOutput(BaseModel):
    reply: str = Field(description="Mensagem a enviar ao usuário em português brasileiro")
    user_name: str | None = None
    is_patient: bool | None = None           # contact IS the patient (true) or schedules for someone else (false)
    patient_name: str | None = None
    is_returning_patient: bool | None = None # patient already attends the clinic (true) or new patient (false)
    preferred_doctor: Literal["julio", "bruna"] | None = None
    birth_date: str | None = None
    birth_date_parse_failed: bool = False
    guardian_relationship: str | None = None
    guardian_name: str | None = None
    guardian_cpf: str | None = None
    patient_email: str | None = None
    patient_cpf: str | None = None
    medication_note: str | None = None
    consultation_reason: str | None = None
    referral_professional: str | None = None
    is_complete: bool = False

    @model_validator(mode="before")
    @classmethod
    def track_birth_date_failure(cls, data: dict) -> dict:
        bd = data.get("birth_date")
        if isinstance(bd, str) and bd.strip() and _parse_birth_date(bd.strip()) is None:
            data["birth_date_parse_failed"] = True
        return data

    @field_validator("birth_date", mode="before")
    @classmethod
    def normalise_birth_date(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return _parse_birth_date(v)  # None if truly unparseable
        return None
