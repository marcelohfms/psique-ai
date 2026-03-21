import re
from pydantic import BaseModel, Field, field_validator
from typing import Literal

_BIRTH_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


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
    def validate_birth_date(cls, v: object) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and _BIRTH_DATE_RE.match(v):
            return v
        return None  # coerce invalid format to None so the node can ask again
