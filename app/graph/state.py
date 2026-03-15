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

    # Is the consultation for the contact themselves or someone else
    is_for_self: bool | None

    # Patient data (may differ from contact when is_for_self=False)
    patient_name: str | None
    patient_age: int | None        # determines 1h vs 2h slot

    # Clinic status
    is_patient: bool | None
    preferred_doctor: Literal["julio", "bruna"] | None
