from typing import Literal
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from app.uazapi import send_text


@tool
async def schedule_appointment(
    preferred_day: str,
    preferred_shift: Literal["manha", "tarde", "noite"],
    config: RunnableConfig,
) -> str:
    """Verifica disponibilidade e agenda uma consulta para o paciente."""
    # TODO: Google Calendar integration
    return (
        f"Horários disponíveis para {preferred_day} ({preferred_shift}): "
        "[integração com Google Calendar pendente]"
    )


@tool
async def request_document(
    document_type: Literal["laudo", "exame", "relatorio", "receita", "declaracao"],
    config: RunnableConfig,
) -> str:
    """Registra uma solicitação de documento médico para o paciente."""
    # TODO: integrate with clinic system
    return f"Solicitação de {document_type} registrada com sucesso."


@tool
async def transfer_to_human(
    reason: str,
    config: RunnableConfig,
) -> str:
    """Transfere a conversa para um atendente humano quando o bot não consegue ajudar."""
    phone = config["configurable"]["phone"]
    await send_text(
        phone,
        "👤 Vou transferir você para um de nossos atendentes. Um momento, por favor!",
    )
    return "Conversa transferida para atendente humano."
