from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from typing import Literal

# Formats the LLM commonly produces; we normalise all of them to dd/mm/yyyy.
_BIRTH_DATE_FORMATS = [
    "%d/%m/%Y",   # already correct
    "%Y-%m-%d",   # ISO 8601
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%m/%d/%Y",   # US format (last resort)
]


def _parse_birth_date(value: str) -> str | None:
    """Try to parse *value* with known formats and return it as dd/mm/yyyy.
    Returns None if no format matches (genuinely unparseable)."""
    for fmt in _BIRTH_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


class CollectInfoOutput(BaseModel):
    reply: str = Field(description="Mensagem a enviar ao usuário em português brasileiro")
    user_name: str | None = None
    is_for_self: bool | None = None
    patient_name: str | None = None
    patient_age: int | None = None
    is_patient: bool | None = None
    preferred_doctor: Literal["julio", "bruna"] | None = None
    birth_date: str | None = None
    guardian_relationship: str | None = None
    guardian_name: str | None = None
    guardian_cpf: str | None = None
    patient_email: str | None = None
    consultation_reason: str | None = None
    referral_professional: str | None = None
    is_complete: bool = False

    @field_validator("birth_date", mode="before")
    @classmethod
    def normalise_birth_date(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            return _parse_birth_date(v)  # None if truly unparseable
        return None
