"""Tests for app/graph/graph.py — o roteamento de entrada do grafo.

Caso Bernardo Lima Beltrão Teixeira (5581987415206, 31/07/2026): guardian_relationship
ficou vazio, is_registration_complete() passou a devolver False e o _route_entry
mandou TODO turno para o collect_info — um nó sem ferramentas. O comprovante que a
mãe enviou foi lido e respondido ("recebemos!"), mas register_payment nunca foi
chamada, a taxa ficou NULL e o cron cancelou a consulta 4h depois.
"""
from langchain_core.messages import AIMessage, HumanMessage

from langgraph.graph import END

from app.graph.graph import _route_entry

RECEIPT = (
    "[imagem]: COMPROVANTE DE PAGAMENTO: valor transferido R$ 100,00, chave PIX "
    "42.006.848/0001-78. [drive_link:https://drive.google.com/file/d/abc/view]"
)

# Cadastro do Bernardo no momento em que o comprovante chegou: completo, exceto
# guardian_relationship ("Para meu filho" não diz se quem fala é mãe ou pai).
_INCOMPLETE = {
    "user_name": "Raphaelle Beltrão",
    "patient_name": "Bernardo Lima Beltrão Teixeira",
    "patient_email": "rlabanatomia@gmail.com",
    "birth_date": "18/03/2016",
    "patient_age": 10,
    "preferred_doctor": "julio",
    "is_patient": False,
    "is_returning_patient": False,
    "guardian_name": "Raphaelle Beltrão",
    "guardian_cpf": "05473966438",
    "guardian_relationship": None,
}


def _state(stage, messages, **over):
    return {**_INCOMPLETE, "stage": stage, "messages": messages, **over}


def test_receipt_reaches_patient_agent_even_when_stage_is_collect_info():
    """O comprovante chegou com stage=collect_info — exatamente o estado do
    Bernardo. Sem ferramentas não há register_payment, então o turno precisa ir
    para o patient_agent."""
    assert _route_entry(_state("collect_info", [HumanMessage(content=RECEIPT)])) == "patient_agent"


def test_receipt_reaches_patient_agent_when_registration_is_incomplete():
    """Mesmo com stage=patient_agent, a guarda de cadastro incompleto empurrava o
    turno de volta para o collect_info. Comprovante não pode depender do cadastro:
    register_payment resolve o paciente pelo telefone/consulta."""
    assert _route_entry(_state("patient_agent", [HumanMessage(content=RECEIPT)])) == "patient_agent"


def test_non_receipt_message_still_falls_back_to_collect_info():
    """A guarda continua valendo para todo o resto — cadastro incompleto não pode
    agendar."""
    assert _route_entry(_state("patient_agent", [HumanMessage(content="Data")])) == "collect_info"


def test_medical_document_is_not_treated_as_receipt():
    doc = "[imagem]: LAUDO: laudo neuropsicológico de 12 páginas."
    assert _route_entry(_state("patient_agent", [HumanMessage(content=doc)])) == "collect_info"


def test_old_receipt_in_history_does_not_hijack_a_later_turn():
    """Só a mensagem que está sendo processada conta. Um comprovante antigo no
    histórico já foi tratado e não pode desviar turnos seguintes."""
    messages = [
        HumanMessage(content=RECEIPT),
        AIMessage(content="Recebemos o comprovante!"),
        HumanMessage(content="Qual o endereço da clínica?"),
    ]
    assert _route_entry(_state("patient_agent", messages)) == "collect_info"


def test_assistant_message_quoting_a_receipt_is_not_a_receipt():
    quoting = AIMessage(content=RECEIPT)
    assert _route_entry(_state("patient_agent", [quoting])) == "collect_info"


def test_receipt_with_a_caption_reaches_patient_agent():
    """Legenda + comprovante é o formato que os dois webhooks produzem
    (`f"{caption}\\n{descrição}"`, app/main.py). Havia 4 casos reais em `messages`
    que o roteamento não reconhecia — o mesmo desfecho do caso Bernardo."""
    msg = HumanMessage(content=f"Pagamento ok\n{RECEIPT}")
    assert _route_entry(_state("collect_info", [msg])) == "patient_agent"


def test_receipt_after_another_image_reaches_patient_agent():
    """O buffer de debounce junta as mensagens da janela numa string só."""
    msg = HumanMessage(content=f"[imagem]: EXAME: hemograma completo. {RECEIPT}")
    assert _route_entry(_state("collect_info", [msg])) == "patient_agent"


def test_typed_claim_of_payment_is_not_a_receipt():
    """Só o classificador de visão cria um comprovante. Texto solto não pode
    furar a guarda de cadastro incompleto e ir agendar."""
    msg = HumanMessage(content="Segue o comprovante de pagamento")
    assert _route_entry(_state("patient_agent", [msg])) == "collect_info"


def test_complete_registration_still_routes_to_patient_agent():
    state = _state("patient_agent", [HumanMessage(content="oi")], guardian_relationship="mãe")
    assert _route_entry(state) == "patient_agent"


# ─── _route_patient_agent: retomada após injeção de ToolMessage ────────────────
#
# Caso Elisabete/Isaac (5581987385089, 02/08/2026): a avó confirmou o novo horário
# ("Sim") do reagendamento, o fast-path de pending_appointment chamou
# confirm_appointment direto, a guarda "paciente já tem consulta agendada" disparou
# e o node injetou (AIMessage com tool_call + ToolMessage com a instrução interna
# "chame mark_reschedule_in_progress"). Só que _route_patient_agent olha apenas
# tool_calls da ÚLTIMA mensagem — e a última era a ToolMessage. O turno terminou em
# END: a LLM nunca viu a instrução e a paciente não recebeu nada. Eva travou.

from langchain_core.messages import ToolMessage

from app.graph.graph import _route_patient_agent
from app.graph.nodes import RESUME_AFTER_TOOL


def _injected_pair(content, *, resume):
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "confirm_appointment", "args": {}, "id": "tc1", "type": "tool_call"}],
    )
    tm = ToolMessage(
        content=content,
        tool_call_id="tc1",
        additional_kwargs={RESUME_AFTER_TOOL: True} if resume else {},
    )
    return [ai, tm]


def test_injected_tool_result_routes_back_to_the_llm():
    """A instrução interna injetada precisa voltar para a LLM agir sobre ela."""
    messages = _injected_pair(
        "[INSTRUÇÃO INTERNA] O paciente já tem consulta(s) agendada(s): 03/08/2026 às 11:00. "
        "OBRIGATÓRIO: chame imediatamente mark_reschedule_in_progress.",
        resume=True,
    )
    assert _route_patient_agent({"messages": messages}) == "patient_agent"


def test_unmarked_tool_result_still_ends_the_turn():
    """A injeção do guard de transferência já enviou a mensagem e já executou a
    transferência — retomar a LLM ali faria a Eva falar depois do handoff."""
    messages = _injected_pair("Transferido para atendimento humano.", resume=False)
    assert _route_patient_agent({"messages": messages}) == END


def test_llm_tool_calls_still_route_to_tools():
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "get_available_slots", "args": {}, "id": "tc2", "type": "tool_call"}],
    )
    assert _route_patient_agent({"messages": [ai]}) == "tools"


def test_plain_answer_ends_the_turn():
    assert _route_patient_agent({"messages": [AIMessage(content="Bom dia!")]}) == END
