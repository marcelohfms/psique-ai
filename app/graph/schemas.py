from pydantic import BaseModel, Field
from typing import Literal


class CollectInfoOutput(BaseModel):
    reply: str = Field(description="Mensagem a enviar ao usuário em português brasileiro")
    user_name: str | None = None
    is_for_self: bool | None = None
    patient_name: str | None = None
    patient_age: int | None = None
    is_patient: bool | None = None
    preferred_doctor: Literal["julio", "bruna"] | None = None
    birth_date: str | None = None
    guardian_name: str | None = None
    guardian_cpf: str | None = None
    patient_email: str | None = None
    consultation_reason: str | None = None
    referral_professional: str | None = None
    is_complete: bool = False
