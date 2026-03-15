from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import ConversationState
from app.graph.schemas import CollectInfoOutput
from app.graph.tools import schedule_appointment, request_document, transfer_to_human
from app.uazapi import send_text
from app.database import upsert_user, DOCTOR_IDS

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [schedule_appointment, request_document, transfer_to_human]

_collect_llm = None
_agent_llm = None


def _get_collect_llm():
    global _collect_llm
    if _collect_llm is None:
        _collect_llm = ChatOpenAI(model="gpt-4o", temperature=0).with_structured_output(CollectInfoOutput)
    return _collect_llm


def _get_agent_llm():
    global _agent_llm
    if _agent_llm is None:
        _agent_llm = ChatOpenAI(model="gpt-4o", temperature=0).bind_tools(TOOLS)
    return _agent_llm


# ── Prompts ───────────────────────────────────────────────────────────────────

_COLLECT_SYSTEM = """\
Você é a assistente virtual da Clínica Psique, uma clínica de psiquiatria.
Sua tarefa é coletar as seguintes informações do usuário, UMA de cada vez, \
de forma natural e acolhedora em português brasileiro.

Informações necessárias (em ordem):
1. user_name        — nome de quem está entrando em contato
2. is_for_self      — a consulta é para a própria pessoa (true) ou outra (false)
3. patient_name     — nome do paciente (pule se is_for_self=true, use user_name)
4. patient_age      — idade do paciente em anos
5. is_patient       — o paciente já é paciente da clínica?
6. preferred_doctor — médico preferido: "julio" (Dr. Júlio) ou "bruna" (Dra. Bruna)

Estado atual dos dados coletados:
{collected}

Regras:
- Colete apenas UMA informação por mensagem.
- Se is_for_self=true, defina patient_name = user_name sem perguntar.
- Só marque is_complete=true quando TODOS os 6 campos estiverem preenchidos.
- Seja acolhedor e empático — a clínica cuida de saúde mental.
- Responda SEMPRE em português brasileiro.
"""

_EXISTING_PATIENT_SYSTEM = """\
Você é a assistente virtual da Clínica Psique atendendo {patient_name} \
({patient_age} anos), paciente do(a) {doctor}.

Você pode ajudar com:
- Agendamento de consultas → use schedule_appointment
- Solicitação de documentos (laudo, exame, relatório, receita, declaração) → use request_document
- Transferência para atendente humano → use transfer_to_human

Seja breve, acolhedor e objetivo. Responda sempre em português brasileiro.
Se for a primeira interação após a coleta de dados, apresente as opções disponíveis.
"""

_NEW_PATIENT_SYSTEM = """\
Você é a assistente virtual da Clínica Psique atendendo {patient_name} \
({patient_age} anos), um novo paciente.

A Clínica Psique é especializada em saúde mental, oferecendo atendimento \
psiquiátrico humanizado com o Dr. Júlio e a Dra. Bruna, \
que atendem adultos, crianças e adolescentes.

O paciente escolheu ser atendido por {doctor}. Sua tarefa é agendar a \
primeira consulta usando schedule_appointment.
Se necessário, transfira para atendente humano com transfer_to_human.

Seja acolhedor e explique brevemente a clínica se o paciente perguntar.
Responda sempre em português brasileiro.
"""

# ── Nodes ─────────────────────────────────────────────────────────────────────

async def collect_info_node(state: ConversationState, config: RunnableConfig) -> dict:
    collected = {
        "user_name": state.get("user_name"),
        "is_for_self": state.get("is_for_self"),
        "patient_name": state.get("patient_name"),
        "patient_age": state.get("patient_age"),
        "is_patient": state.get("is_patient"),
        "preferred_doctor": state.get("preferred_doctor"),
    }

    messages = [
        SystemMessage(content=_COLLECT_SYSTEM.format(collected=collected)),
        *state["messages"],
    ]

    result: CollectInfoOutput = await _get_collect_llm().ainvoke(messages)

    await send_text(state["phone"], result.reply)

    update: dict = {"messages": [AIMessage(content=result.reply)]}

    for field in ["user_name", "is_for_self", "patient_name", "patient_age", "is_patient", "preferred_doctor"]:
        val = getattr(result, field)
        if val is not None:
            update[field] = val

    if result.is_complete:
        update["stage"] = "patient_agent"

        # Merge collected fields with existing state for the upsert
        merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}
        await upsert_user(state["phone"], {
            "name": merged.get("user_name"),
            "patient_name": merged.get("patient_name"),
            "age": merged.get("patient_age"),
            "is_patient": merged.get("is_patient"),
            "doctor_id": DOCTOR_IDS.get(merged.get("preferred_doctor", ""), None),
        })

    return update


async def patient_agent_node(state: ConversationState, config: RunnableConfig) -> dict:
    """
    Single LLM call per turn. If the LLM returns tool calls, the graph routes
    to the ToolNode. When it returns plain text, it sends to WhatsApp and ends.
    """
    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    template = _EXISTING_PATIENT_SYSTEM if state.get("is_patient") else _NEW_PATIENT_SYSTEM
    system_prompt = template.format(
        patient_name=state.get("patient_name") or state.get("user_name", "paciente"),
        patient_age=state.get("patient_age", ""),
        doctor=doctor_label,
    )

    messages = [SystemMessage(content=system_prompt), *state["messages"]]
    response = await _get_agent_llm().ainvoke(messages)

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        await send_text(state["phone"], response.content)

    return {"messages": [response]}
