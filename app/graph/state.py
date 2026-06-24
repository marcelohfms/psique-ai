from typing import Annotated, Literal
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages


ConversationStage = Literal["collect_info", "patient_agent", "human_handoff"]


class ConversationState(TypedDict):
    # LangGraph message history
    messages: Annotated[list, add_messages]

    # WhatsApp sender ID (e.g. "5583...@s.whatsapp.net")
    phone: str

    # Current stage in the conversation flow
    stage: ConversationStage

    # Who is contacting
    user_name: str | None

    # Patient data (may differ from contact when is_patient=False)
    patient_name: str | None
    patient_age: int | None        # determines 1h vs 2h slot

    # Clinic status
    is_patient: bool | None           # True = contact IS the patient; False = contact schedules for someone else
    is_returning_patient: bool | None # True = patient already attends the clinic (returning); False = new patient
    preferred_doctor: Literal["julio", "bruna"] | None

    # Relationship of contact to patient (only relevant when is_patient=False and patient is minor)
    guardian_relationship: str | None

    # Extended patient registration fields
    birth_date: str | None          # data de nascimento (dd/mm/aaaa)
    guardian_name: str | None       # nome dos pais/responsáveis (menores)
    guardian_cpf: str | None        # CPF dos pais/responsáveis (menores)
    patient_email: str | None       # e-mail para contato
    patient_cpf: str | None          # CPF do paciente
    consultation_reason: str | None # motivo da consulta (apenas novos pacientes)
    referral_professional: str | None  # profissional que encaminhou (apenas novos pacientes)
    medication_note: str | None      # medicação solicitada (apenas para receitas)

    # Dados do responsável financeiro (para nota fiscal — pode ser diferente do paciente)
    financial_name: str | None
    financial_cpf: str | None
    financial_email: str | None

    # When True, Eva executes silently (attendant instruction via private note):
    # posts result as a Chatwoot private note instead of sending a WhatsApp message.
    silent_mode: bool | None

    # Multi-patient support: when a phone has more than one patient, stores the list
    # while the bot waits for the guardian to pick one.
    pending_patients: list | None

    # Holds a candidate patient dict while Eva waits for the guardian to confirm
    # the selection before advancing to patient_agent.
    pending_confirmation_patient: dict | None

    # DB id of the patient selected during disambiguation (used for targeted upserts).
    user_db_id: str | None

    # Modality restriction from DB: "online", "presencial", or None (no restriction)
    modality_restriction: Literal["online", "presencial"] | None

    # Age exception from DB: True = patient bypasses doctor age limits
    age_exception: bool | None

    # Carries the user's original intent from collect_info → patient_agent (same turn).
    # Set when collect_info completes during a document or scheduling request so
    # patient_agent_node knows what to do immediately without being distracted by
    # unrelated context (e.g. upcoming appointments).
    # Cleared by patient_agent_node after injecting the directive.
    pending_action: str | None

    # Stores appointment data extracted from the confirmation summary Eva sent the patient.
    # When set and the patient replies affirmatively, patient_agent_node calls
    # confirm_appointment directly (bypassing the LLM) to prevent double-booking.
    # Keys: slot_datetime (ISO), slot_duration_minutes, modality, doctor
    pending_appointment: dict | None
