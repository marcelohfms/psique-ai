import os
import re as _re_mod
from datetime import datetime
from zoneinfo import ZoneInfo
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage
from langchain_core.runnables import RunnableConfig

from app.graph.state import ConversationState
from app.graph.schemas import CollectInfoOutput
from app.graph.tools import (
    get_available_slots, confirm_appointment,
    cancel_appointment, cancel_all_appointments, reschedule_appointment, mark_reschedule_in_progress,
    keep_original_appointment,
    change_modality,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    set_social_name,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data, extend_payment_deadline, waive_booking_fee,
    request_external_contact, nudge_external_contact,
    _expected_consultation_amount,
)
from app.graph.prompts import COLLECT_SYSTEM, MINOR_RULE, MINOR_RETURNING_RULE, ADULT_RULE, GUARDIAN_RULE, EXISTING_PATIENT_SYSTEM, NEW_PATIENT_SYSTEM, CANCELLATION_RULES, MODALITY_CHANGE_RULE, CLINIC_ADDRESS, CLINIC_ADDRESS_TEXT, get_doctors_info, sanitize_clinic_address, get_booking_fee_rule, MEDICAL_LIMITS_RULE, AGE_EXCEPTION_RULE, DOCTOR_CORRECTION_RULE, EMAIL_RULE, get_pricing_rules, ATTENDANT_INSTRUCTION_RULE, get_pricing_exception_rule, CORRECT_PIX_KEY, SOCIAL_NAME_RULE, MINOR_FIRST_CONSULT_INFO, MINOR_RULE_SCHEDULING_ONLY, NO_REPEAT_ANSWER_RULE
from app.whatsapp import send_text
from app.database import upsert_user, log_event, get_upcoming_appointments, get_user_by_phone, get_users_by_phone, DOCTOR_IDS, DOCTOR_NAMES, save_message, get_last_assistant_message_time, is_registration_complete, missing_registration_field
from app.chatwoot import get_conversation_id, add_private_note
from app.utils import looks_like_name

# ── LLM setup (lazy — instantiated on first use after .env is loaded) ─────────

TOOLS = [
    get_available_slots, confirm_appointment,
    cancel_appointment, cancel_all_appointments, reschedule_appointment, mark_reschedule_in_progress,
    keep_original_appointment,
    change_modality,
    request_document, transfer_to_human, confirm_attendance,
    register_payment, update_preferred_doctor, save_patient_email,
    set_social_name,
    register_refund_request, confirm_refund_completed,
    request_registration_update, nudge_doctor_document,
    consultar_data, extend_payment_deadline, waive_booking_fee,
    request_external_contact, nudge_external_contact,
]

_collect_llm = None
_agent_llm = None

# Marca (em additional_kwargs) uma ToolMessage injetada por um guard do
# patient_agent_node cujo resultado a LLM ainda precisa ler e agir sobre.
# _route_patient_agent devolve o turno ao patient_agent quando a última mensagem
# tem essa marca — sem ela o turno morre em END com a instrução interna sem leitor
# (caso Elisabete/Isaac, 5581987385089, 02/08/2026). Injeções que já enviaram a
# resposta ao paciente e já executaram a ação (guard de transferência) NÃO levam
# a marca: retomar a LLM ali faria a Eva falar depois do handoff.
RESUME_AFTER_TOOL = "eva_resume_after_tool"


# ── History trimming helpers ──────────────────────────────────────────────────

def _emitted_tool_call_ids(msg) -> list:
    """Todos os tool_call_ids que uma AIMessage emitiu — tool_calls válidos,
    invalid_tool_calls (args truncados pelo cap de tokens) e a cópia crua em
    additional_kwargs.tool_calls que alguns providers deixam. Um id em qualquer
    um desses lugares exige uma ToolMessage de resposta, senão a OpenAI dá 400."""
    ids: list = []
    for bucket in (
        getattr(msg, "tool_calls", None) or [],
        getattr(msg, "invalid_tool_calls", None) or [],
        (getattr(msg, "additional_kwargs", None) or {}).get("tool_calls") or [],
    ):
        for tc in bucket:
            tid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            if tid:
                ids.append(tid)
    return ids


def _strip_orphan_tool_calls(messages: list) -> list:
    """
    Sanitiza o histórico para nunca violar a regra de pareamento de tool-calls da
    OpenAI ("uma assistant message com 'tool_calls' precisa ser seguida por tool
    messages respondendo cada tool_call_id; e toda tool message precisa responder
    a um tool_call anterior"). Neutraliza os DOIS lados do desbalanceamento:

    - AIMessage que emite tool_call(s) sem TODA resposta correspondente → dropada.
    - ToolMessage cujo tool_call_id nenhuma AIMessage (mantida) emitiu → dropada.

    O segundo caso é o que bricka o thread para sempre: uma ToolMessage órfã fica
    na lista de mensagens persistida, então TODO turno seguinte manda o histórico
    corrompido pra OpenAI e leva 400, o grafo grava __error__ e morre antes de a
    Eva responder — e, diferente de um __error__ transitório, NÃO se auto-cura
    (caso Kimmy/Darleide, 5581999656460, muda de 20/07 a 18/08/2026). Sanitizar
    aqui, no caminho que monta a chamada da OpenAI, garante que um único erro de
    tool não envenene o thread permanentemente.
    """
    raw_messages = list(messages)

    # tool_call_ids que alguma ToolMessage de fato responde.
    responded_ids = {
        getattr(m, "tool_call_id", None)
        for m in raw_messages
        if getattr(m, "type", None) == "tool"
    }
    responded_ids.discard(None)

    # Uma AIMessage com tool_calls só é mantida se TODOS os ids emitidos foram
    # respondidos. Guardamos os ids das mantidas para saber quais ToolMessages
    # têm um emissor válido. (responded_ids é fixo, então uma passada basta.)
    valid_emitted_ids: set = set()
    for msg in raw_messages:
        if getattr(msg, "type", None) == "ai":
            eids = _emitted_tool_call_ids(msg)
            if eids and all(e in responded_ids for e in eids):
                valid_emitted_ids.update(eids)

    clean_messages = []
    for msg in raw_messages:
        mtype = getattr(msg, "type", None)
        if mtype == "ai":
            eids = _emitted_tool_call_ids(msg)
            if eids and not all(e in responded_ids for e in eids):
                continue  # AIMessage com tool_call sem resposta → dropa
        elif mtype == "tool":
            if getattr(msg, "tool_call_id", None) not in valid_emitted_ids:
                continue  # ToolMessage órfã (sem AIMessage emissora) → dropa
        clean_messages.append(msg)
    return clean_messages


def _trim_history(messages: list, max_hist: int = None) -> list:
    """
    Trim conversation history to cap token usage.
    - Removes orphan tool_calls from messages.
    - If len(messages) > max_hist, keeps only the last max_hist messages.
    - Always ensures the first kept message is a human message (no orphan tool-response).

    Args:
        messages: List of message objects (must already be orphan-stripped).
        max_hist: Maximum number of messages to keep. Uses MAX_HISTORY_MESSAGES env var if not provided.

    Returns:
        Trimmed list of messages.
    """
    if max_hist is None:
        max_hist = int(os.getenv("MAX_HISTORY_MESSAGES", "30"))

    clean_messages = _strip_orphan_tool_calls(messages)

    if len(clean_messages) > max_hist:
        clean_messages = clean_messages[-max_hist:]
        # Walk forward until the first message is a human message
        while clean_messages and getattr(clean_messages[0], "type", "") != "human":
            clean_messages = clean_messages[1:]

    return clean_messages


def _with_transient_retry(runnable):
    """Retry com backoff para queda transitória de rede/DNS na chamada do LLM.

    O SDK da OpenAI já re-tenta rápido (2x em segundos); esta camada cobre
    quedas um pouco mais longas, que hoje matam o run e deixam o paciente sem
    resposta (caso Norma/Ian, 5581988007007, 01/08/2026: ConnectError de DNS).
    O retry fica AQUI, na chamada do LLM (sem side effects), e não no nó — os
    nós enviam mensagens via send_text, e re-executar o nó inteiro reenviaria
    mensagens já entregues.
    """
    import openai
    return runnable.with_retry(
        retry_if_exception_type=(openai.APIConnectionError,),
        wait_exponential_jitter=True,
        stop_after_attempt=3,
    )


def _get_collect_llm():
    global _collect_llm
    if _collect_llm is None:
        _collect_llm = _with_transient_retry(
            ChatOpenAI(model="gpt-5.6-luna", reasoning_effort="none").with_structured_output(CollectInfoOutput)
        )
    return _collect_llm


def _get_agent_llm():
    global _agent_llm
    if _agent_llm is None:
        _agent_llm = _with_transient_retry(
            # gpt-5.6-luna: temperature fixa em 1 e tools na chat API exigem reasoning_effort="none"
            ChatOpenAI(model="gpt-5.6-luna", reasoning_effort="none").bind_tools(TOOLS)
        )
    return _agent_llm


# Verbos/expressões de COMPROMISSO de emitir/solicitar um documento. A Eva usa
# essas fórmulas quando entendeu o pedido e "prometeu" o documento — é exatamente
# nesse momento que ela deveria ter chamado request_document.
_DOC_COMMIT_MARKERS = (
    "vou solicitar", "vou emitir", "vou providenciar", "vou pedir",
    "vou encaminhar o pedido", "estou solicitando", "já solicitei",
    "solicitei a emissão", "encaminhei o pedido",
    "será enviad", "será emitid", "será providenciad", "vou gerar",
)

# Deferimento LEGÍTIMO: recibo/nota fiscal de consulta que ainda vai acontecer
# (prompt, linhas 1154-1158). Aqui a Eva corretamente NÃO chama request_document —
# o guard não pode interpretar isso como promessa quebrada.
_DOC_DEFER_MARKERS = (
    "depois que a consulta", "após a consulta", "depois da consulta",
    "quando a consulta acontecer", "quando a consulta ocorrer",
    "assim que a consulta", "só pode ser providenciad", "só posso emitir depois",
    "só pode ser emitid", "após a realização",
)


def _detect_document_promise(text: str) -> str | None:
    """Se a resposta da Eva se COMPROMETE a emitir/solicitar um documento, retorna
    o document_type inferido (mesmo enum de request_document); senão None.

    Espelha, no lado do texto, o GUARD_RECEIPT_REGISTER: a Eva já violou o prompt
    prometendo "vou solicitar a nota fiscal... tudo bem?" sem chamar a ferramenta
    (caso Carlos Henrique, 5581995163026, 24/08/2026), deixando o pedido sem
    nenhum registro. Retornar o tipo aqui permite ao nó injetar request_document.
    """
    if not text:
        return None
    low = text.lower()
    # Deferimento legítimo (consulta futura) não é promessa de emissão agora.
    if any(m in low for m in _DOC_DEFER_MARKERS):
        return None
    # Precisa haver um verbo de compromisso — só mencionar o documento (oferta,
    # pergunta "qual você prefere?") não conta.
    if not any(m in low for m in _DOC_COMMIT_MARKERS):
        return None
    return _document_type_from_text(low)


def _document_type_from_text(low: str) -> str | None:
    """Mapeia o tipo de documento (mesmo enum de request_document) a partir de um
    texto já em minúsculas — do mais específico para o mais genérico. None se
    nenhum tipo aparece."""
    if "nota fiscal" in low or "nota-fiscal" in low:
        return "nota_fiscal"
    if "recibo" in low and ("plano de saúde" in low or "plano de saude" in low or "recibo saúde" in low or "recibo saude" in low):
        return "nota_fiscal"
    if "recibo" in low:
        return "recibo"
    if "laudo" in low:
        return "laudo"
    if "exame" in low:
        return "exame"
    if "relatório" in low or "relatorio" in low:
        return "relatorio"
    if "receita" in low:
        return "receita"
    if "declaração" in low or "declaracao" in low:
        return "declaracao"
    if "atestado" in low:
        return "atestado"
    if "requisição" in low or "requisicao" in low or "encaminhamento" in low:
        return "requisicao"
    return None


# Verbos FORTES de emissão na fala do PACIENTE: pedem um documento NOVO ser
# emitido. Presentes, disparam mesmo que o paciente também diga que vai "buscar"
# depois (caso Davi: "providencie uma receita para irmos buscar segunda").
_EMISSION_STRONG_MARKERS = (
    "providenci", "emitir", "emita", "emite", "renov", "gerar", "gere",
    "solicit", "fazer a receita", "fazer uma receita", "faça a receita",
    "faz a receita", "acaband", "acabou", "acabaram", "faltando", "faltou",
    "não veio", "nao veio", "não saiu", "nao saiu", "sem a receita", "vencid",
    "vencend", "nova via", "segunda via", "2ª via", "2a via",
)

# Marcadores FRACOS de necessidade: sozinhos indicam pedido, mas viram retirada
# quando acompanhados de um marcador de retirada abaixo.
_EMISSION_WEAK_MARKERS = (
    "preciso", "precisa", "queria", "gostaria", "pode fazer", "podem fazer",
    "pode emitir", "podem emitir",
)

# Retirada de documento JÁ pronto — sem verbo forte de emissão, isto NÃO é pedido
# de emissão (o registro seria espúrio). Ex.: "vim buscar minha receita pronta".
_EMISSION_RETRIEVAL_MARKERS = (
    "buscar", "pegar", "retirar", "retirada", "pronta", "pronto",
    "já está", "ja esta", "já ficou", "ja ficou", "disponível", "disponivel",
)


def _detect_document_emission_request(text: str) -> str | None:
    """Se a MENSAGEM DO PACIENTE pede a EMISSÃO de um documento, retorna o
    document_type inferido (mesmo enum de request_document); senão None.

    Espelha, no lado do pedido do paciente, o que _detect_document_promise faz no
    lado da resposta da Eva. Serve ao guard do caminho de handoff: quando a Eva
    roteia para transfer_to_human (verbo "buscar", urgência, fora do horário) num
    turno em que o paciente pediu emissão, o pedido não é registrado por lugar
    nenhum (casos Davi/Daniel e Eduardo Lyra — receita-providenciar-vs-buscar.md).

    Conservador para não gravar pedido espúrio: exige um TIPO de documento e um
    verbo forte de emissão — OU um marcador fraco de necessidade sem nenhum
    marcador de retirada (que indicaria documento já pronto)."""
    if not text:
        return None
    low = text.lower()
    doc_type = _document_type_from_text(low)
    if not doc_type:
        return None
    has_strong = any(m in low for m in _EMISSION_STRONG_MARKERS)
    has_weak = any(m in low for m in _EMISSION_WEAK_MARKERS)
    has_retrieval = any(m in low for m in _EMISSION_RETRIEVAL_MARKERS)
    if has_strong or (has_weak and not has_retrieval):
        return doc_type
    return None


def _detect_debora_insistence(messages: list) -> bool:
    """Detecta se o paciente está insistindo em falar com Débora sem responder às mensagens de Eva.

    Retorna True se:
    1. O paciente mencionou "débora" 2 ou mais vezes
    2. E está sendo teimoso (não respondendo substantivamente às perguntas de Eva)

    A detecção distingue entre:
    - Insistência: "Débora?" "Débora, você pode falar?" (puro foco em Débora)
    - Resposta com menção: "Sou João, mas quero falar com Débora" (respondendo + mencionando)
    """
    if not messages:
        return False

    human_msgs = [m for m in messages if getattr(m, "type", None) == "human"]
    if not human_msgs:
        return False

    debora_mentions = 0
    substantive_responses = 0

    for msg in human_msgs:
        content = (msg.content or "").lower()

        has_debora = "débora" in content or "debora" in content
        if has_debora:
            debora_mentions += 1

        # Verificar se esta é uma resposta substantiva (informação real sendo fornecida)
        # Mesmo que tenha "débora", se tem informação é uma resposta válida
        info_keywords = [
            "sou ", "meu nome ", "chamo ", "minha data", "nascimento",
            "email", "@", "ano", "dia", "mês", "mes",
            "/",  # data separator — forte indicador de data
        ]
        # Procurar por números (datas, idades) ou palavras de informação
        has_number = any(c.isdigit() for c in content)
        has_info_keyword = any(kw in content for kw in info_keywords)

        if (has_number and "/" in content) or has_info_keyword:
            substantive_responses += 1

    # Trigger insistência se:
    # - Há 2+ menções de Débora
    # - E NENHUMA resposta substantiva foi dada
    # (Se o paciente está respondendo perguntas, mesmo mencionando Débora, não é insistência)
    if debora_mentions >= 2 and substantive_responses == 0:
        return True

    return False


def _registration_state_to_user(d: dict) -> dict:
    """Map the conversation state onto the dict shape missing_registration_field()
    expects. Used both to warn the LLM about the field it still owes and to check
    its answer afterwards."""
    return {
        "name": d.get("user_name"),
        "email": d.get("patient_email"),
        "birth_date": d.get("birth_date"),
        "doctor_id": DOCTOR_IDS.get(d.get("preferred_doctor", ""), None),
        "is_patient": d.get("is_patient"),
        "is_returning_patient": d.get("is_returning_patient"),
        "patient_name": d.get("patient_name"),
        "age": d.get("patient_age"),
        "guardian_name": d.get("guardian_name"),
        "guardian_cpf": d.get("guardian_cpf"),
        "guardian_relationship": d.get("guardian_relationship"),
    }


# Pergunta determinística para cada campo obrigatório do cadastro. Serve de rede
# quando a LLM diz que o cadastro está completo mas missing_registration_field()
# discorda: sem isso ninguém volta a perguntar e a conversa fica presa no
# collect_info para sempre (caso Bernardo, 5581987415206, 31/07/2026).
_REGISTRATION_QUESTIONS = {
    "name": "Antes de continuar, como posso te chamar?",
    "email": (
        "Preciso de um e-mail para contato — é por ele que enviamos o Termo de "
        "Compromisso e os documentos da consulta. Qual e-mail podemos usar?"
    ),
    "birth_date": "Qual a data de nascimento do paciente? (formato dd/mm/aaaa)",
    "doctor_id": "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?",
    # O texto precisa conter "agendando em nome" — é por essa frase que o bloco
    # mais abaixo reconhece que a pergunta foi feita e aceita o is_patient extraído
    # pela LLM. Com outra redação a resposta do paciente é descartada e a pergunta
    # se repete para sempre.
    "is_patient": (
        "Só para eu registrar certo: você é o(a) paciente ou está agendando em "
        "nome de outra pessoa?"
    ),
    "is_returning_patient": (
        "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    ),
    "patient_name": "Qual o nome completo do paciente?",
    "guardian_name": "Qual o nome completo do responsável pelo paciente?",
    "guardian_relationship": "E qual o seu parentesco com {patient_first}? (mãe, pai, avó, responsável legal...)",
    "guardian_cpf": "Qual é o CPF do responsável?",
}


# Parentescos reconhecidos, do mais específico para o mais genérico ("avó" antes
# de "av" nunca; "irmã" antes de "irmão" é irrelevante — a ordem só importa para
# prefixos como "madrasta"/"mãe").
_RELATIONSHIP_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("madrasta",), "madrasta"),
    (("padrasto",), "padrasto"),
    (("madrinha",), "madrinha"),
    (("padrinho",), "padrinho"),
    (("responsável legal", "responsavel legal", "tutor", "tutora", "guarda"), "responsável legal"),
    (("mãe", "mae", "genitora"), "mãe"),
    (("pai", "genitor"), "pai"),
    (("avó", "avo", "avô"), "avó/avô"),
    (("irmã", "irma", "irmão", "irmao"), "irmão/irmã"),
    (("tia", "tio"), "tia/tio"),
]


def _normalize_relationship(text: str) -> str:
    """Extrai o parentesco de uma resposta livre ("sou a mãe dele" → "mãe").

    Nunca devolve vazio: se nada for reconhecido, guarda a resposta do paciente
    como veio. Um retorno vazio faria o passo do cadastro repetir a mesma pergunta
    indefinidamente — o modo de falha que este campo já causou uma vez."""
    raw = (text or "").strip()
    low = raw.lower()
    for needles, label in _RELATIONSHIP_KEYWORDS:
        if any(_re_mod.search(r'(?<!\w)' + _re_mod.escape(n) + r'(?!\w)', low) for n in needles):
            return label
    return raw


_NOT_PATIENT_KWS = (
    "não", "nao", "mãe", "mae", "pai", "filho", "filha",
    "em nome", "para meu", "para minha", "esposo", "esposa",
    "marido", "irmão", "irmao", "irma", "outra", "outra pessoa",
)

# Precisam de limite de palavra: "eu" está dentro de "meu", e "meu" aparece em
# "para meu filho", que significa exatamente o contrário.
_SELF_PATIENT_KWS = ("sim", "eu", "mim", "comigo", "minha consulta", "própria", "propria")


def _classify_is_patient_answer(text: str, user_name: str) -> tuple[bool | None, str | None]:
    """Lê a resposta de "A consulta é para você ou para outra pessoa?".

    Devolve (is_patient, patient_name). is_patient=None significa que a resposta
    não decide nada e a pergunta deve ser refeita.

    Antes era só lista negra — is_pat = not any(palavra_de_negação) — então a
    AUSÊNCIA de negação virava afirmação. Responder com o nome do paciente, que é
    resposta natural, caía no lado errado: caso Beatriz (5587996089614,
    04/08/2026), a mãe respondeu "Beatriz Loyola Gomes de Vasconcélos", o passo
    concluiu is_patient=True (invertendo um False já correto) e copiou o user_name
    para patient_name.

    True é o lado destrutivo: copia user_name para patient_name e pula a pergunta
    do nome do paciente, sem recuperação. Por isso ele agora exige sinal positivo;
    na dúvida, pergunta de novo.
    """
    h = (text or "").strip().lower()
    if not h:
        return None, None
    if any(kw in h for kw in _NOT_PATIENT_KWS):
        return False, None
    if any(_re_mod.search(rf'(?<!\w){_re_mod.escape(kw)}(?!\w)', h) for kw in _SELF_PATIENT_KWS):
        return True, None
    # Responder com um nome quer dizer "é para essa pessoa" — a menos que seja o
    # próprio nome de quem está no WhatsApp, que quer dizer "sou eu".
    if looks_like_name(text):
        if h == (user_name or "").strip().lower():
            return True, None
        return False, text.strip()
    return None, None


def _registration_question(field: str, merged: dict) -> str:
    """Deterministic question for a missing registration field."""
    from app.utils import display_name as _dn
    template = _REGISTRATION_QUESTIONS.get(field)
    if not template:
        return ""
    patient_full = merged.get("patient_name") or merged.get("user_name") or "o paciente"
    return template.format(patient_first=_dn(patient_full))


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def collect_info_node(state: ConversationState, config: RunnableConfig) -> dict:
    collected = {
        "user_name": state.get("user_name"),
        "is_patient": state.get("is_patient"),
        "patient_name": state.get("patient_name"),
        "birth_date": state.get("birth_date"),
        "guardian_relationship": state.get("guardian_relationship"),
        "guardian_name": state.get("guardian_name"),
        "guardian_cpf": state.get("guardian_cpf"),
        "is_patient": state.get("is_patient"),
        "preferred_doctor": state.get("preferred_doctor"),
        "patient_email": state.get("patient_email"),
        "consultation_reason": state.get("consultation_reason"),
        "referral_professional": state.get("referral_professional"),
    }

    # ── Disambiguation: confirmação de paciente selecionado ──────────────────────
    if state.get("pending_confirmation_patient") is not None:
        candidate = state["pending_confirmation_patient"]
        last_human = ""
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "human":
                last_human = (msg.content or "").strip().lower()
                break

        _affirmative = {"sim", "s", "yes", "y", "isso", "correto", "certo", "exato", "confirmado", "confirmo", "ok"}
        _negative    = {"não", "nao", "no", "n", "errado", "incorreto", "outro", "outra"}

        # Implicit confirmation: the reply doesn't say "sim" but already names the
        # candidate patient (e.g. "agendar para Maria José às 11h") — treat as confirmed
        # instead of falling through to "não entendi", which silently drops the selection.
        _candidate_name = (candidate.get("patient_name") or candidate.get("name") or "").lower()
        _candidate_parts = [p for p in _candidate_name.split() if len(p) > 2]
        _implicit_confirm = bool(_candidate_parts) and any(p in last_human for p in _candidate_parts)

        if any(w in last_human for w in _affirmative) or _implicit_confirm:
            doc_key = DOCTOR_NAMES.get(candidate.get("doctor_id", ""), None)
            return {
                "pending_confirmation_patient": None,
                "pending_patients": None,
                "user_db_id": candidate["id"],
                "user_name": candidate.get("name"),
                "patient_name": candidate.get("patient_name") or candidate.get("name"),
                "patient_age": candidate.get("age"),
                "birth_date": candidate.get("birth_date"),
                "is_patient": candidate.get("is_patient"),
                "is_returning_patient": candidate.get("is_returning_patient"),
                "preferred_doctor": doc_key,
                "patient_email": candidate.get("email"),
                "guardian_name": candidate.get("guardian_name"),
                "guardian_cpf": candidate.get("guardian_cpf"),
                "guardian_relationship": candidate.get("guardian_relationship"),
                "patient_cpf": candidate.get("patient_cpf"),
                "modality_restriction": candidate.get("modality_restriction"),
                "age_exception": candidate.get("age_exception"),
                "stage": "patient_agent",
                "messages": [],
            }
        elif any(w in last_human for w in _negative):
            all_pending = state.get("pending_patients") or []
            names = [u.get("patient_name") or u.get("name") or "Paciente" for u in all_pending]
            options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
            reply = f"Sem problema! Para qual paciente você está entrando em contato?\n\n{options}"
            await send_text(state["phone"], reply)
            await save_message(state["phone"], "assistant", reply)
            return {
                "pending_confirmation_patient": None,
                "pending_patients": all_pending,
                "messages": [AIMessage(content=reply)],
            }
        else:
            patient_name = candidate.get("patient_name") or candidate.get("name") or "o paciente"
            reply = f"Desculpe, não entendi. Você está entrando em contato para *{patient_name}*? (sim/não)"
            await send_text(state["phone"], reply)
            await save_message(state["phone"], "assistant", reply)
            return {"messages": [AIMessage(content=reply)]}

    # ── Disambiguation: seleção de paciente da lista ──────────────────────────────
    if state.get("pending_patients"):
        pending = state["pending_patients"]
        last_human = ""
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "human":
                last_human = (msg.content or "").strip().lower()
                break

        selected = None
        for i, u in enumerate(pending):
            name = (u.get("patient_name") or u.get("name") or "").lower()
            name_parts = name.split()
            if str(i + 1) == last_human or any(part in last_human for part in name_parts if len(part) > 2):
                selected = u
                break

        if selected:
            patient_name = selected.get("patient_name") or selected.get("name") or "o paciente"
            reply = f"Só confirmar: você está entrando em contato para *{patient_name}*, certo? (sim/não)"
            await send_text(state["phone"], reply)
            await save_message(state["phone"], "assistant", reply)
            return {
                "pending_confirmation_patient": selected,
                "pending_patients": pending,
                "messages": [AIMessage(content=reply)],
            }
        else:
            names = [u.get("patient_name") or u.get("name") or "Paciente" for u in pending]
            options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
            reply = f"Não consegui identificar. Pode digitar o número ou o nome do paciente?\n\n{options}"
            await send_text(state["phone"], reply)
            await save_message(state["phone"], "assistant", reply)
            return {"messages": [AIMessage(content=reply)]}

    # ── DB load — sempre recarrega do banco, valores do DB sempre prevalecem ──────
    # Garante que campos atualizados externamente (ex: is_returning_patient após
    # consulta concluída) reflitam no estado, independente do checkpoint.
    all_users = await get_users_by_phone(state["phone"])

    if len(all_users) > 1:
        # Auto-select the patient with a scheduled appointment — no disambiguation needed.
        from app.database import get_supabase as _get_supabase
        import datetime as _dt
        _client = await _get_supabase()
        _now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        _user_ids = [u["id"] for u in all_users]
        _appt_result = await (
            _client.from_("appointments")
            .select("patient_id")
            .in_("patient_id", _user_ids)
            .eq("status", "scheduled")
            .gte("start_time", _now)
            .execute()
        )
        _scheduled_user_ids = {r["patient_id"] for r in (_appt_result.data or [])}
        _with_appt = [u for u in all_users if u["id"] in _scheduled_user_ids]
        if len(_with_appt) == 1:
            all_users = _with_appt

    if len(all_users) == 1:
        u = all_users[0]

        # If the existing record is for someone else (is_patient=False) and the
        # contact is now booking for themselves ("para mim"), start a fresh
        # registration instead of loading the third-party record.
        _self_keywords = [
            "para mim", "pra mim", "para eu", "sou eu", "sou a paciente",
            "sou o paciente", "é para mim", "e para mim", "é pra mim",
            "e pra mim", "para mim mesmo", "pra mim mesmo",
        ]
        _last_human_msg = ""
        for _m in reversed(state.get("messages", [])):
            if getattr(_m, "type", "") == "human":
                _last_human_msg = (getattr(_m, "content", "") or "").lower()
                break
        _contact_booking_for_self = (
            u.get("is_patient") is False
            and any(kw in _last_human_msg for kw in _self_keywords)
        )

        if not _contact_booking_for_self:
            doc_key = DOCTOR_NAMES.get(u.get("doctor_id", ""), None)
            loaded = {
                "user_db_id": u["id"],
                "user_name": u.get("name"),
                # Only default patient_name to the contact's own name when the DB
                # already confirms the contact IS the patient. Otherwise (is_patient
                # False/unknown) this would wrongly skip asking for the patient's
                # name in collect_info's step machine (Step 2c).
                "patient_name": u.get("patient_name") or (u.get("name") if u.get("is_patient") else None),
                "patient_age": u.get("age"),
                "birth_date": u.get("birth_date"),
                "is_patient": u.get("is_patient"),
                "is_returning_patient": u.get("is_returning_patient"),
                "preferred_doctor": doc_key,
                "patient_email": u.get("email"),
                "guardian_name": u.get("guardian_name"),
                "guardian_cpf": u.get("guardian_cpf"),
                "guardian_relationship": u.get("guardian_relationship"),
                "patient_cpf": u.get("patient_cpf"),
                "modality_restriction": u.get("modality_restriction"),
                "age_exception": u.get("age_exception"),
            }
            if is_registration_complete(u):
                return {**loaded, "stage": "patient_agent", "messages": []}
            # Incomplete: merge DB values into state so systematic questions use
            # fresh data. DB wins over any stale checkpoint values.
            for k, v in loaded.items():
                if v is not None:
                    state[k] = v  # type: ignore[literal-required]

    elif len(all_users) > 1:
        names = [u.get("patient_name") or u.get("name") or "Paciente" for u in all_users]
        options = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
        reply = f"Olá! Para qual paciente você está entrando em contato?\n\n{options}"
        await send_text(state["phone"], reply)
        await save_message(state["phone"], "assistant", reply)
        return {"pending_patients": all_users, "messages": [AIMessage(content=reply)]}

    # Detect receita request from any user message
    _messages_text = " ".join(
        m.content for m in state["messages"]
        if hasattr(m, "content") and isinstance(m.content, str)
    ).lower()
    _is_receita = "receita" in _messages_text

    # Detect document requests (email is only needed for these, not for scheduling)
    _doc_keywords = ["receita", "laudo", "nota fiscal", "declaração", "declaracao",
                     "relatório", "relatorio", "exame", "atestado",
                     "requisição", "requisicao"]
    _is_document = any(kw in _messages_text for kw in _doc_keywords)

    # Detect if this is the very first bot response (no prior AIMessages)
    _has_greeted = any(getattr(m, "type", None) == "ai" for m in state["messages"])

    # Detect if the user has already made a specific request
    _request_keywords = [
        "receita", "agendar", "consulta", "laudo", "exame",
        "relatório", "relatorio", "nota fiscal", "declaração", "declaracao",
        "requisição", "requisicao",
    ]
    _has_request = any(kw in _messages_text for kw in _request_keywords)

    # Fields that must be merged into EVERY return of this turn (so a doctor
    # mentioned mid-conversation is never lost on subsequent early returns).
    _persistent_updates: dict = {}

    # Detect a doctor preference stated anywhere in the conversation and persist it
    # IMMEDIATELY to the cadastro — e.g. "quero agendar com a Dra. Bruna".
    # Only acts when exactly one doctor is mentioned (avoids ambiguity).
    if not state.get("preferred_doctor"):
        _mentions_bruna = "bruna" in _messages_text
        _mentions_julio = "júlio" in _messages_text or "julio" in _messages_text
        _doc_key = None
        if _mentions_bruna and not _mentions_julio:
            _doc_key = "bruna"
        elif _mentions_julio and not _mentions_bruna:
            _doc_key = "julio"
        if _doc_key:
            state["preferred_doctor"] = _doc_key
            _persistent_updates["preferred_doctor"] = _doc_key
            # Only persist to DB when the patient already exists (user_db_id known).
            # Calling upsert_user before registration creates a contact with name=null
            # and a patient without a patient_contacts link, breaking future lookups.
            if state.get("user_db_id"):
                try:
                    await upsert_user(
                        state["phone"],
                        {"doctor_id": DOCTOR_IDS.get(_doc_key)},
                        user_id=state.get("user_db_id"),
                    )
                except Exception:
                    import logging as _log
                    _log.getLogger(__name__).exception("Failed to persist preferred_doctor")

    def _minor_first_explain_needed(extra: dict | None = None) -> bool:
        """True quando as 3 condições (menor de idade + primeira consulta +
        Dr. Júlio) estão completas neste turno e a explicação ainda não foi
        enviada. `extra` são os campos recém-extraídos neste turno (ainda não
        persistidos em `state`)."""
        merged = {**state, **_persistent_updates}
        if extra:
            merged.update(extra)
        age = merged.get("patient_age") or 99
        return (
            age < 18
            and merged.get("is_returning_patient") is False
            and merged.get("preferred_doctor") == "julio"
            and not merged.get("minor_first_consult_explained")
        )

    def _minor_first_explain_text(extra: dict | None = None) -> str:
        merged = {**state, **_persistent_updates}
        if extra:
            merged.update(extra)
        from app.utils import display_name as _dn
        full_name = (
            merged.get("social_name") or merged.get("patient_name")
            or merged.get("user_name") or "o paciente"
        )
        return MINOR_FIRST_CONSULT_INFO.format(patient_name=_dn(full_name))

    async def _ask(reply: str) -> dict:
        # Force stage back to collect_info: a caller upstream (e.g. an attendant
        # instruction in main.py) may have optimistically set stage="patient_agent"
        # before this turn even ran. Since we're asking another registration
        # question, registration is NOT complete — without this, _route_after_collect
        # would wrongly continue into patient_agent in the same turn, producing a
        # second, conflicting AI message and corrupting _last_ai()/_last_human()
        # bookkeeping for the next turn (see Talita/CPF incident 2026-07-03).
        extra_update: dict = {}
        if _minor_first_explain_needed():
            reply = _minor_first_explain_text() + "\n\n" + reply
            extra_update["minor_first_consult_explained"] = True
        await send_text(state["phone"], reply)
        await save_message(state["phone"], "assistant", reply)
        return {**_persistent_updates, **extra_update, "stage": "collect_info", "messages": [AIMessage(content=reply)]}

    async def _extract_and_ask(extracted: dict, next_q: str) -> dict:
        """Persist extracted fields to Supabase and ask the next question in one turn."""
        _STATE_TO_DB = {
            "user_name": "name",
            "patient_name": "patient_name",
            "patient_cpf": "patient_cpf",
            "birth_date": "birth_date",
            "patient_age": "age",
            "guardian_name": "guardian_name",
            "guardian_cpf": "guardian_cpf",
            "guardian_relationship": "guardian_relationship",
            "is_patient": "is_patient",
            "is_returning_patient": "is_returning_patient",
            "patient_email": "email",
        }
        db_payload = {_STATE_TO_DB[k]: v for k, v in extracted.items() if k in _STATE_TO_DB}
        if "preferred_doctor" in extracted:
            db_payload["doctor_id"] = DOCTOR_IDS.get(extracted["preferred_doctor"])
        extra_update: dict = {}
        if next_q and _minor_first_explain_needed(extracted):
            next_q = _minor_first_explain_text(extracted) + "\n\n" + next_q
            extra_update["minor_first_consult_explained"] = True
        # See _ask() above for why stage is forced back to collect_info here too.
        result_update: dict = {**_persistent_updates, **extracted, **extra_update, "stage": "collect_info", "messages": [AIMessage(content=next_q)]}
        if db_payload:
            try:
                returned_id = await upsert_user(state["phone"], db_payload, user_id=state.get("user_db_id"))
                # O id devolvido pode DIFERIR do enviado: quando o usuário afirma
                # já ser da clínica, o upsert_user reconcilia por nome+nascimento
                # e devolve o cadastro existente (caso Maria José, 10/08/2026).
                # O state precisa acompanhar, senão o agendamento segue no duplicado.
                if returned_id and returned_id != state.get("user_db_id"):
                    result_update["user_db_id"] = returned_id
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception("Failed to persist partial collect_info data")
        await send_text(state["phone"], next_q)
        await save_message(state["phone"], "assistant", next_q)
        return result_update

    def _last_ai() -> str:
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "ai":
                return msg.content or ""
        return ""

    def _last_human() -> str:
        for msg in reversed(state["messages"]):
            if getattr(msg, "type", None) == "human":
                return msg.content or ""
        return ""

    _extracted: dict = {}  # fields extracted programmatically this turn

    _NAME_Q = "Pode me informar o seu nome completo?"
    _IS_PATIENT_Q = "A consulta é para você ou para outra pessoa?"
    _PATIENT_NAME_Q = "Qual o nome completo do paciente?"

    _CPF_Q = "Qual o CPF do paciente?"
    _BIRTH_Q = "Qual a data de nascimento do paciente? (formato dd/mm/aaaa)"
    _GUARDIAN_NAME_Q = "Qual é o nome completo do responsável pelo paciente?"
    _GUARDIAN_CPF_Q = "Qual é o CPF do responsável?"
    _PATIENT_Q = "É a primeira consulta ou o paciente já está em acompanhamento na clínica?"
    _DOCTOR_Q = "Você tem preferência pelo Dr. Júlio ou pela Dra. Bruna?"
    _EMAIL_Q = "Qual o e-mail para envio?"
    _EMAIL_Q_CADASTRO = "Qual o seu e-mail? Precisamos para incluir no seu cadastro. 📋"
    _MED_Q = "Qual medicação você precisa na receita?"

    # ── Ordem canônica das perguntas do cadastro ─────────────────────────────
    # Retorna a próxima pergunta a fazer, ou None quando todos os campos
    # obrigatórios da ramificação atual já estão preenchidos.
    # Pacientes que JÁ são da clínica (is_returning_patient=True) pulam
    # patient_cpf e, para menores, guardian_cpf — esses dados já existem no
    # cadastro da clínica. A idade (patient_age) define a ramificação menor/adulto.
    # _NQ_KEYS é apenas a lista de campos lidos do state por _nq — NÃO é a ordem
    # das perguntas (a ordem canônica é a sequência de checagens em _next_question).
    _NQ_KEYS = (
        "user_name", "is_patient", "patient_name", "birth_date",
        "is_returning_patient", "patient_cpf", "patient_age",
        "guardian_name", "guardian_relationship", "guardian_cpf",
        "preferred_doctor", "patient_email",
    )

    def _guardian_rel_q(s: dict) -> str:
        from app.utils import display_name as _dn
        full = s.get("patient_name") or ""
        quem = f"com {_dn(full)}" if full else "com o paciente"
        return f"E qual o seu parentesco {quem}? (mãe, pai, avó, responsável legal...)"

    def _next_question(s: dict) -> str | None:
        age = s.get("patient_age") or 99
        minor = age < 18
        returning = s.get("is_returning_patient")
        is_new = returning is False
        if not s.get("user_name"):
            return _NAME_Q
        if s.get("is_patient") is None:
            return _IS_PATIENT_Q
        if s.get("is_patient") is False and not s.get("patient_name"):
            return _PATIENT_NAME_Q
        if not s.get("birth_date"):
            return _BIRTH_Q
        if returning is None:
            return _PATIENT_Q
        if is_new and not s.get("patient_cpf"):
            return _CPF_Q
        is_third_party = s.get("is_patient") is False
        if minor and is_third_party and not s.get("guardian_name"):
            return _GUARDIAN_NAME_Q
        # guardian_relationship é exigido por missing_registration_field() para todo
        # menor com um terceiro conversando, mas nunca era perguntado aqui — o campo
        # só era preenchido se a LLM conseguisse inferi-lo da conversa. Quando ela não
        # conseguia ("Para meu filho" não diz mãe nem pai), o cadastro ficava
        # eternamente incompleto e o _route_entry prendia a conversa no collect_info
        # (caso Bernardo, 5581987415206, 31/07/2026). Esta é a correção de raiz; a
        # pergunta determinística no fim do nó continua como rede de segurança.
        if minor and is_third_party and not s.get("guardian_relationship"):
            return _guardian_rel_q(s)
        if minor and is_third_party and is_new and not s.get("guardian_cpf"):
            return _GUARDIAN_CPF_Q
        if not s.get("preferred_doctor"):
            return _DOCTOR_Q
        if not s.get("patient_email"):
            return _EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO
        return None

    def _nq(**extra) -> str | None:
        merged = {k: state.get(k) for k in _NQ_KEYS}
        merged.update(extra)
        return _next_question(merged)

    def _is_info_question(text: str) -> bool:
        """True quando o paciente interrompe o cadastro com uma pergunta de
        informação (valor, endereço, convênio, pagamento, duração, localização).
        Nesses casos a Eva deve RESPONDER e repetir a pergunta pendente — nunca
        tratar o texto como resposta do campo atual (caso Arthur/Lúcio: 'Qual
        valor da consulta?' era engolido como CPF inválido, 2026-07-20; caso Davi
        27/07/2026: 'O atendimento é no rio mar?' ignorado, não detectado).

        Whitelist em vez de blacklist: se a mensagem contém uma interrogação ou
        palavras de busca de informação, é provavelmente uma pergunta, não uma
        resposta de campo."""
        tl = (text or "").lower()
        # Pergunta explícita (tem interrogação) + palavras-chave de informação
        _info_kws = [
            "valor", "valores", "preço", "preco", "quanto custa", "quanto é",
            "quanto e", "quanto fica", "custa", "custo", "endereço", "endereco",
            "onde fica", "onde é", "onde e", "como funciona", "convênio", "convenio",
            "plano de saúde", "plano de saude", "aceita plano", "quanto tempo",
            "duração", "duracao", "forma de pagamento", "formas de pagamento",
            "aceita cartão", "aceita cartao", "parcela", "localiz", "atendimento é",
            "atendimento esta", "funciona em", "agendam",
        ]
        has_question_mark = "?" in (text or "")
        has_info_keyword = any(kw in tl for kw in _info_kws)
        # Qualquer pergunta (?) + informação sobre clínica/agendamento = interrupção
        if has_question_mark and has_info_keyword:
            return True
        # Contexto: se o médico/clínica são mencionados em pergunta, é info
        if has_question_mark and any(x in tl for x in ["doutor", "dra.", "dra ", "médico", "dr.", "dr ", "clínica", "clinica"]):
            return True
        return False

    # Uma pergunta de informação durante o cadastro (FASE 2) é roteada ao LLM, que
    # tem as regras de preço/endereço e responde antes de retomar a pergunta pendente.
    _is_interruption = (
        _has_request
        and _nq() is not None
        and _is_info_question(_last_human())
    )

    # Step 1: greeting + first MISSING question (skip fields already in state)
    if not _has_greeted and _has_request:
        first_q = _nq()
        if first_q:
            greeting = (
                "Olá! 😊 Sou a Eva, assistente virtual da Clínica Psique.\n\n"
                "Claro, posso te ajudar com isso! Mas primeiro precisarei colher algumas informações.\n\n"
                + first_q
            )
            return await _ask(greeting)

    # Steps 2-8 only run when user has made a specific request — but NOT when the
    # patient interrupted with an info question (that turn is answered by the LLM).
    if _has_request and not _is_interruption:
        from app.graph.schemas import _parse_birth_date
        last_ai = _last_ai()
        last_human = _last_human().strip()
        # Strip the attendant-note prefix before using last_human as a raw
        # extracted value (CPF, email, etc). Without this, an attendant pasting
        # a field value in a private note (e.g. "[Instrução da atendente]: 010.022.724-40")
        # gets the whole prefixed string saved verbatim into the field.
        _ATTENDANT_PREFIX = "[Instrução da atendente]: "
        if last_human.startswith(_ATTENDANT_PREFIX):
            last_human = last_human[len(_ATTENDANT_PREFIX):].strip()

        # Step 2: contact name — saved to contacts.name only
        if not state.get("user_name"):
            if last_ai and _NAME_Q in last_ai and last_human:
                # O caminho gêmeo do patient_name (Step 2c) já validava; este não,
                # e gravava qualquer coisa que chegasse depois da pergunta. Foi
                # assim que o texto de um comprovante virou nome de contato
                # (5581991812399) — e o upsert_user copia o nome do contato para
                # o paciente quando o paciente ainda não tem nome, então o valor
                # ruim contaminava as duas tabelas de uma vez.
                if not looks_like_name(last_human):
                    return await _ask(
                        "Não consegui identificar o nome. Pode me informar o seu nome completo?"
                    )
                _s2_extracted: dict = {"user_name": last_human}
                if state.get("preferred_doctor"):
                    _s2_extracted["preferred_doctor"] = state["preferred_doctor"]
                return await _extract_and_ask(_s2_extracted, _nq(user_name=last_human))
            return await _ask(_NAME_Q)

        # Step 2b: is the contact the patient or scheduling for someone else?
        # If is_patient came from DB hydration (not explicitly answered this conversation),
        # ask again to confirm — avoids assuming stale or incorrect DB values.
        if state.get("is_patient") is not None and not state.get("_is_patient_confirmed"):
            _IS_PATIENT_CONFIRM_Q = _IS_PATIENT_Q  # reuse same question
            _asked_confirm = (
                "para você ou" in last_ai.lower()
                or "para outra pessoa" in last_ai.lower()
                or _IS_PATIENT_Q in last_ai
            )
            if _asked_confirm and last_human:
                h = last_human.lower()
                is_pat, _answered_name = _classify_is_patient_answer(
                    last_human, state.get("user_name") or ""
                )
                if is_pat is None:
                    # Nem negação, nem afirmação, nem nome: reperguntar é melhor que
                    # assumir. Ver _classify_is_patient_answer para o porquê de True
                    # ser o lado destrutivo.
                    return await _ask(_registration_question("is_patient", state))
                if is_pat:
                    _uname = state.get("user_name", "")
                    return await _extract_and_ask(
                        {"is_patient": True, "patient_name": _uname, "_is_patient_confirmed": True},
                        _nq(is_patient=True, patient_name=_uname),
                    )
                else:
                    _not_patient_update: dict = {"is_patient": False, "_is_patient_confirmed": True}
                    _uname = state.get("user_name") or ""
                    if _uname:
                        _not_patient_update["guardian_name"] = _uname
                    # The contact was previously assumed/confirmed to be the patient
                    # (patient_name == user_name). Now they say it's for someone else —
                    # clear patient_name so collect_info re-asks for the real patient's name
                    # instead of silently keeping the contact's name as the patient's.
                    if state.get("patient_name") and state.get("patient_name") == state.get("user_name"):
                        _not_patient_update["patient_name"] = None
                    # A resposta JÁ trouxe o nome do paciente ("Beatriz Loyola Gomes
                    # de Vasconcélos" para "é para você ou para outra pessoa?").
                    # Aproveitar evita repetir a pergunta que a pessoa acabou de
                    # responder.
                    if _answered_name:
                        _not_patient_update["patient_name"] = _answered_name
                    return await _extract_and_ask(_not_patient_update, _nq(**_not_patient_update))
            return await _ask(_IS_PATIENT_CONFIRM_Q)

        if state.get("is_patient") is None:
            _asked_is_patient = (
                _IS_PATIENT_Q in last_ai
                or "para você ou" in last_ai.lower()
                or "para outra pessoa" in last_ai.lower()
            )
            if _asked_is_patient and last_human:
                h = last_human.lower()
                is_pat, _answered_name = _classify_is_patient_answer(
                    last_human, state.get("user_name") or ""
                )
                if is_pat is None:
                    # Nem negação, nem afirmação, nem nome: reperguntar é melhor que
                    # assumir. Ver _classify_is_patient_answer para o porquê de True
                    # ser o lado destrutivo.
                    return await _ask(_registration_question("is_patient", state))
                if is_pat:
                    _uname = state.get("user_name", "")
                    return await _extract_and_ask(
                        {"is_patient": True, "patient_name": _uname, "_is_patient_confirmed": True},
                        _nq(is_patient=True, patient_name=_uname),
                    )
                else:
                    # Infer guardian relationship and name from the same message.
                    # e.g. "minha filha" → relationship="mãe", guardian=contact name.
                    _rel_map = [
                        (["filha", "filho"],        "mãe/pai"),
                        (["mãe", "mae", "mamãe"],   "filho(a)"),
                        (["pai", "papai"],           "filho(a)"),
                        (["esposa", "marido", "esposo", "cônjuge", "conjuge"], "cônjuge"),
                        (["irmã", "irma", "irmão", "irmao"], "irmão/irmã"),
                    ]
                    _inferred_rel = None
                    for _kws, _rel in _rel_map:
                        if any(kw in h for kw in _kws):
                            _inferred_rel = _rel
                            break
                    _not_patient_update: dict = {"is_patient": False, "_is_patient_confirmed": True}
                    # Pre-populate guardian info from the contact's own name
                    _uname = state.get("user_name") or ""
                    if _uname:
                        _not_patient_update["guardian_name"] = _uname
                    if _inferred_rel:
                        _not_patient_update["guardian_relationship"] = _inferred_rel
                    # See Step 2b above: don't let a stale patient_name==user_name
                    # (e.g. hydrated from an earlier turn/DB state) block the
                    # patient-name question now that we know it's for someone else.
                    if state.get("patient_name") and state.get("patient_name") == state.get("user_name"):
                        _not_patient_update["patient_name"] = None
                    # A resposta JÁ trouxe o nome do paciente ("Beatriz Loyola Gomes
                    # de Vasconcélos" para "é para você ou para outra pessoa?").
                    # Aproveitar evita repetir a pergunta que a pessoa acabou de
                    # responder.
                    if _answered_name:
                        _not_patient_update["patient_name"] = _answered_name
                    return await _extract_and_ask(
                        _not_patient_update, _nq(**_not_patient_update)
                    )
            return await _ask(_IS_PATIENT_Q)

        # Step 2c: patient name (only when contact is scheduling for someone else)
        if state.get("is_patient") is False and not state.get("patient_name"):
            if last_ai and _PATIENT_NAME_Q in last_ai and last_human:
                if looks_like_name(last_human):
                    return await _extract_and_ask(
                        {"patient_name": last_human}, _nq(patient_name=last_human)
                    )
                return await _ask("Não consegui identificar o nome. Pode informar o nome completo do paciente?")
            return await _ask(_PATIENT_NAME_Q)

        # Step 3: birth date — calcula a idade
        if not state.get("birth_date"):
            asked_birth = (
                "nascimento" in last_ai.lower()
                or "dd/mm/aaaa" in last_ai.lower()
                or "não consegui identificar a data" in last_ai.lower()
            )
            if asked_birth and last_human:
                parsed = _parse_birth_date(last_human)
                if parsed:
                    bd = datetime.strptime(parsed, "%d/%m/%Y")
                    today = datetime.now()
                    age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
                    return await _extract_and_ask(
                        {"birth_date": parsed, "patient_age": age},
                        _nq(birth_date=parsed, patient_age=age),
                    )
                else:
                    return await _ask("Não consegui identificar a data. Pode informar no formato dd/mm/aaaa? Ex: 15/01/1990.")
            return await _ask(_BIRTH_Q)

        # Step 4: is_returning_patient
        # "já é paciente da clínica?" → True = retornante, False = novo.
        # is_patient (contato é o paciente vs. agenda para outro) é separado e
        # NÃO deve ser setado aqui.
        if state.get("is_returning_patient") is None:
            if last_ai and _PATIENT_Q in last_ai and last_human:
                h = last_human.lower()
                # Frases inequívocas primeiro — incluindo negações que INVERTEM o
                # sentido ("não é a primeira" = já é paciente). Só depois caímos na
                # polaridade simples (não/sim), evitando classificar errado quem
                # responde com uma negação ("não, não é a primeira vez").
                _returning_phrases = [
                    "acompanhamento", "já sou", "ja sou", "já estou", "ja estou",
                    "já faço", "ja faco", "sou paciente", "já é paciente", "ja e paciente",
                    "já fui", "ja fui", "já tenho", "ja tenho", "retorno",
                    "não é a primeira", "nao e a primeira", "não é primeira", "nao e primeira",
                    "não primeira", "nao primeira",
                ]
                _new_phrases = [
                    "primeira vez", "primeira consulta", "é a primeira", "e a primeira",
                    "nunca", "novo", "nova", "não sou paciente", "nao sou paciente",
                    "ainda não", "ainda nao",
                ]
                if any(p in h for p in _returning_phrases):
                    is_returning_patient = True
                elif any(p in h for p in _new_phrases):
                    is_returning_patient = False
                elif any(kw in h for kw in ["não", "nao", "primeira"]):
                    is_returning_patient = False
                elif any(kw in h for kw in ["sim", "já", "ja", "sou", "é", "paciente"]):
                    is_returning_patient = True
                else:
                    is_returning_patient = None
                if is_returning_patient is not None:
                    return await _extract_and_ask(
                        {"is_returning_patient": is_returning_patient},
                        _nq(is_returning_patient=is_returning_patient),
                    )
            return await _ask(_PATIENT_Q)

        # Step 5: CPF do paciente — apenas para pacientes NOVOS; opcional p/ estrangeiros
        if state.get("is_returning_patient") is False and not state.get("patient_cpf"):
            # O guard reconhece tanto a pergunta original quanto o reprompt
            # "CPF inválido..." — senão o CPF válido enviado logo após o reprompt
            # cai no re-ask e é ignorado (caso Arthur/Lúcio, 2026-07-20).
            _last_ai_asked_cpf = last_ai and (
                _CPF_Q in last_ai or "cpf inválido" in last_ai.lower()
            )
            if _last_ai_asked_cpf and last_human:
                import re as _re
                _foreign_kws = [
                    "não tenho", "nao tenho", "estrangeiro", "estrangeira",
                    "não possuo", "nao possuo", "passport", "passaporte",
                    "sem cpf", "não tem cpf", "nao tem cpf",
                ]
                if any(kw in last_human.lower() for kw in _foreign_kws):
                    return await _extract_and_ask({"patient_cpf": "N/A"}, _nq(patient_cpf="N/A"))
                elif _re.search(r'\d', last_human):
                    return await _extract_and_ask({"patient_cpf": last_human}, _nq(patient_cpf=last_human))
                else:
                    return await _ask(
                        "CPF inválido. Por favor, informe o CPF do paciente com os números (ex: 123.456.789-10).\n"
                        "Caso o paciente não tenha CPF (estrangeiro), responda \"não tenho CPF\"."
                    )
            return await _ask(_CPF_Q)

        # Step 6: guardian name (menores com um terceiro conversando —
        # is_patient=False; um menor autoatendido não tem responsável na
        # conversa para exigir isso, ver caso Clara 2026-07-21)
        if (state.get("patient_age") or 99) < 18 and state.get("is_patient") is False \
                and not state.get("guardian_name"):
            _last_ai_asked_guardian_name = last_ai and (
                _GUARDIAN_NAME_Q in last_ai
                or "responsável" in last_ai.lower()
                or "nome completo do" in last_ai.lower()
            )
            if _last_ai_asked_guardian_name and last_human:
                # Quarto caminho que gravava nome sem validar — o #126 fechou os do
                # user_name, do patient_name e o da extração da LLM, e este ficou de
                # fora. Pior: ele escreve em guardian_name E user_name, passando por
                # cima da validação do Step 2 e corrompendo também o nome do contato,
                # que o upsert_user então copia para o paciente. Caso Beatriz
                # (5587996089614, 04/08/2026): "Por gentileza, veja se ele consegue
                # atender na quinta-feira" virou nome do responsável.
                if not looks_like_name(last_human):
                    return await _ask(
                        "Não consegui identificar o nome. Qual é o nome completo "
                        "do responsável pelo paciente?"
                    )
                return await _extract_and_ask(
                    {"guardian_name": last_human, "user_name": last_human},
                    _nq(guardian_name=last_human),
                )
            return await _ask(_GUARDIAN_NAME_Q)

        # Step 6b: parentesco do responsável (menores com terceiro conversando).
        # Perguntar sem um passo que capture a resposta faria a pergunta repetir
        # para sempre, então este passo é obrigatório junto com _next_question.
        if (state.get("patient_age") or 99) < 18 and state.get("is_patient") is False \
                and not state.get("guardian_relationship"):
            if last_ai and "parentesco" in last_ai.lower() and last_human:
                _rel = _normalize_relationship(last_human)
                return await _extract_and_ask(
                    {"guardian_relationship": _rel},
                    _nq(guardian_relationship=_rel),
                )
            return await _ask(_guardian_rel_q({
                "patient_name": state.get("patient_name"),
            }))

        # Step 7: guardian CPF (menores com terceiro conversando) — apenas
        # para pacientes NOVOS; opcional p/ estrangeiros
        if (state.get("patient_age") or 99) < 18 \
                and state.get("is_patient") is False \
                and state.get("is_returning_patient") is False \
                and not state.get("guardian_cpf"):
            _last_ai_asked_guardian_cpf = last_ai and (
                _GUARDIAN_CPF_Q in last_ai
                or ("cpf" in last_ai.lower() and "responsável" in last_ai.lower())
                or ("cpf" in last_ai.lower() and (state.get("guardian_name") or "").split()[0].lower() in last_ai.lower())
            )
            if _last_ai_asked_guardian_cpf and last_human:
                _foreign_kws = [
                    "não tenho", "nao tenho", "estrangeiro", "estrangeira",
                    "não possuo", "nao possuo", "passport", "passaporte",
                    "sem cpf", "não tem cpf", "nao tem cpf",
                ]
                if any(kw in last_human.lower() for kw in _foreign_kws):
                    return await _extract_and_ask({"guardian_cpf": "N/A"}, _nq(guardian_cpf="N/A"))
                return await _extract_and_ask({"guardian_cpf": last_human}, _nq(guardian_cpf=last_human))
            return await _ask(_GUARDIAN_CPF_Q)

        # Step 9: preferred doctor
        if not state.get("preferred_doctor"):
            _last_ai_asked_doctor = last_ai and (
                _DOCTOR_Q in last_ai
                or "júlio" in last_ai.lower()
                or "julio" in last_ai.lower()
                or "bruna" in last_ai.lower()
                or "médico" in last_ai.lower()
                or "preferência" in last_ai.lower()
            )
            if _last_ai_asked_doctor and last_human:
                h = last_human.lower()
                # "sem preferência" → escolher automaticamente respeitando a regra
                # de idade e a agenda mais próxima (caso Arthur/Lúcio, 2026-07-20).
                _no_pref_kws = [
                    "qualquer", "tanto faz", "indiferente", "sem preferência",
                    "sem preferencia", "não tenho pref", "nao tenho pref",
                    "nenhum", "os dois", "vocês escolh", "voces escolh",
                    "o que tiver", "mais cedo", "mais rápido", "mais rapido",
                    "mais próximo", "mais proximo", "pode ser qualquer",
                ]
                _auto_picked = False
                if "julio" in h or "júlio" in h:
                    doctor = "julio"
                elif "bruna" in h:
                    doctor = "bruna"
                elif any(kw in h for kw in _no_pref_kws):
                    from app.graph.tools import pick_doctor_by_earliest_availability
                    age = state.get("patient_age") or 99
                    age_exception = state.get("age_exception")
                    # Júlio atende até 65 (sem limite superior se age_exception);
                    # Bruna atende a partir de 12 anos, sem limite superior.
                    candidates = []
                    if age <= 65 or age_exception:
                        candidates.append("julio")
                    if age >= 12:
                        candidates.append("bruna")
                    if not candidates:
                        candidates = ["julio"]  # fallback defensivo
                    if len(candidates) == 1:
                        doctor = candidates[0]
                    else:
                        _minor_first = age < 18 and state.get("is_returning_patient") is False
                        _slot_dur = 120 if _minor_first else 60
                        doctor = await pick_doctor_by_earliest_availability(candidates, _slot_dur)
                    _auto_picked = bool(doctor)
                else:
                    doctor = None
                if doctor:
                    next_email_q = _EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO
                    if _auto_picked:
                        _doctor_label = "o Dr. Júlio" if doctor == "julio" else "a Dra. Bruna"
                        next_email_q = (
                            f"Sem problemas! Como você não tem preferência, seguirei com "
                            f"{_doctor_label}, que tem a agenda mais próxima. 😊\n\n"
                            + next_email_q
                        )
                    return await _extract_and_ask({"preferred_doctor": doctor}, next_email_q)
            return await _ask(_DOCTOR_Q)

        # Step 10: email — ALWAYS required, whether scheduling or requesting a document
        if not state.get("patient_email"):
            _last_ai_asked_email = last_ai and (
                _EMAIL_Q in last_ai
                or _EMAIL_Q_CADASTRO in last_ai
                or "e-mail" in last_ai.lower()
                or "email" in last_ai.lower()
            )
            if _last_ai_asked_email and last_human and "@" in last_human:
                if _is_receita and not state.get("medication_note"):
                    return await _extract_and_ask({"patient_email": last_human}, _MED_Q)
                else:
                    # Last step — save and fall through to LLM to confirm
                    _extracted["patient_email"] = last_human
                    collected["patient_email"] = last_human
            elif _last_ai_asked_email and last_human and "@" not in last_human:
                return await _ask("E-mail inválido. Por favor, informe um e-mail válido (ex: nome@email.com).")
            else:
                return await _ask(_EMAIL_Q if _is_document else _EMAIL_Q_CADASTRO)

        # Step 11: medication — only for receita (last step)
        if _is_receita and not state.get("medication_note"):
            if last_ai and _MED_Q in last_ai and last_human:
                # Last step — save and fall through to LLM to confirm
                _extracted["medication_note"] = last_human
                collected["medication_note"] = last_human
            else:
                return await _ask(_MED_Q)

        # All programmatic steps complete — _extracted will be merged into update below

    _collect_system = COLLECT_SYSTEM.format(
        collected=collected,
        pricing_rules=get_pricing_rules(datetime.now()),
        # A FASE 1 e as interrupções mandam a Eva responder dúvidas de endereço e
        # sobre os médicos "usando as regras acima" — sem estes dois blocos ela
        # inventa a resposta (caso Davi/Jacarandás, 27/07/2026).
        clinic_address=CLINIC_ADDRESS,
        doctors_info=get_doctors_info(),
        medical_limits_rule=MEDICAL_LIMITS_RULE,
    )
    # Interrupção com pergunta de informação: instrua o LLM a responder a dúvida e
    # retomar exatamente a pergunta pendente do cadastro, sem pular etapas.
    if _is_interruption:
        _pending_q = _nq()
        _collect_system += (
            f"\n\nO paciente interrompeu o cadastro com uma pergunta: "
            f"\"{_last_human()}\". Responda a dúvida de forma clara e objetiva usando "
            f"as regras acima (preços, endereço, formas de pagamento, etc.). "
            f"EM SEGUIDA, na MESMA mensagem, retome o cadastro repetindo exatamente "
            f"esta pergunta pendente, sem pular etapas: \"{_pending_q}\". "
            f"NÃO marque is_complete=true e NÃO extraia nenhum campo deste turno."
        )
    # Inject contact-vs-patient rule when already known during collect phase
    _ci_is_third_party = state.get("is_patient") is False
    _ci_user_name = state.get("user_name")
    _ci_patient_name = state.get("patient_name")
    if _ci_is_third_party and _ci_user_name and _ci_patient_name and _ci_user_name != _ci_patient_name:
        from app.utils import display_name as _dn
        _ci_contact_first = _dn(_ci_user_name)
        _ci_patient_first = _dn(_ci_patient_name)
        _collect_system += (
            f"\n\nIMPORTANTE — CONTATO ≠ PACIENTE: Quem está no WhatsApp é *{_ci_user_name}* "
            f"(o contato/responsável), NÃO o paciente *{_ci_patient_first}*. "
            f"Em TODAS as mensagens, dirija-se a {_ci_contact_first} pelo nome. "
            f"Use o nome {_ci_patient_first} apenas quando falar SOBRE o paciente na terceira pessoa."
        )

    # Diga à LLM, em texto, qual campo o gate determinístico ainda exige. Sem isso
    # ela declara o cadastro completo com um campo obrigatório vazio, o
    # _route_entry devolve todo turno para cá, e ninguém mais pergunta nada.
    # Só depois que a sequência programática se esgota (_nq() is None): as duas
    # listas cobrem os mesmos campos em ordens diferentes (missing_registration_field
    # cobra o e-mail em 2º, _next_question em último), e avisar antes faria a Eva
    # furar a ordem canônica das perguntas.
    _still_missing = (
        missing_registration_field(_registration_state_to_user(state))
        if _nq() is None else None
    )
    if _still_missing and not _is_interruption:
        _collect_system += (
            f"\n\nCAMPO OBRIGATÓRIO AINDA FALTANDO: {_still_missing}. "
            f"NÃO marque is_complete=true e NÃO diga que o cadastro está completo "
            f"enquanto ele não for preenchido — pergunte por ele agora, "
            f"a menos que o paciente tenha acabado de respondê-lo nesta mensagem."
        )

    # Trim history to cap token usage (same as patient_agent_node).
    _trimmed_messages = _trim_history(state["messages"])

    messages = [
        SystemMessage(content=_collect_system),
        *_trimmed_messages,
    ]

    result: CollectInfoOutput = await _get_collect_llm().ainvoke(messages)

    # Only show parse error if the LLM provided a non-empty string that failed validation.
    birth_date_invalid = (
        result.birth_date_parse_failed
        and state.get("birth_date") is None
    )

    reply = result.reply
    if birth_date_invalid:
        reply = (
            "Não consegui identificar a data de nascimento no formato correto. "
            "Poderia informar no formato dd/mm/aaaa? Por exemplo: 15/01/1994."
        )

    # Endereço inventado nunca chega ao paciente — nem ao histórico, senão a LLM
    # repete a alucinação nos turnos seguintes (caso Davi/Jacarandás, 27/07/2026).
    reply, _addr_fixed = sanitize_clinic_address(reply)
    if _addr_fixed:
        import logging as _log_addr
        _log_addr.getLogger(__name__).warning(
            "GUARD_WRONG_ADDRESS: endereço inventado no collect_info corrigido phone=%s original=%s",
            state["phone"], result.reply,
        )

    # A mensagem só é enviada no fim deste nó: a resposta da LLM ainda pode ser
    # substituída por uma pergunta determinística se ela declarar o cadastro
    # completo com um campo obrigatório vazio (ver abaixo).
    update: dict = {}

    for field in [
        "user_name", "patient_name",
        "birth_date", "patient_cpf", "guardian_relationship", "guardian_name", "guardian_cpf",
        "is_returning_patient", "preferred_doctor", "patient_email",
        "consultation_reason", "referral_professional", "medication_note",
    ]:
        val = getattr(result, field, None)
        if val is None:
            continue
        # Os passos programáticos validam o nome antes de gravar, mas a LLM
        # escreve nestes mesmos campos por conta própria e ninguém conferia —
        # a mesma porta com outra fechadura. Um nome que não parece nome é
        # descartado aqui; o passo determinístico volta a perguntar no próximo
        # turno, em vez de deixar o corpo de uma mensagem virar prontuário.
        if field in ("user_name", "patient_name", "guardian_name") and not looks_like_name(val):
            import logging as _log
            _log.getLogger(__name__).warning(
                "NOME_DESCARTADO: LLM extraiu %s=%r que não parece nome phone=%s",
                field, val[:120], state["phone"],
            )
            continue
        update[field] = val

    # is_patient must ONLY be set via programmatic steps (Step 4 for minors, Step 4d for adults).
    # The LLM must not infer it from context — this avoids silently setting is_patient=False
    # without asking the "Você é o(a) paciente?" question.
    # Only apply the LLM-extracted value if the programmatic steps haven't set it yet AND the
    # last AI message actually asked about it (meaning the programmatic step 4d ran and the
    # user answered, but somehow the extraction loop above is handling it).
    if result.is_patient is not None and state.get("is_patient") is None:
        _last_ai_text = ""
        for _m in reversed(state.get("messages", [])):
            if getattr(_m, "type", None) == "ai":
                _last_ai_text = (_m.content or "").lower()
                break
        _asked_is_patient = (
            "agendando em nome" in _last_ai_text
            or ("você é" in _last_ai_text and "paciente" in _last_ai_text)
        )
        if _asked_is_patient:
            update["is_patient"] = result.is_patient

    # Merge any fields extracted programmatically this turn
    for k, v in _extracted.items():
        update[k] = v

    # If the LLM just extracted preferred_doctor for the first time, persist it to DB
    # immediately so it's not lost if is_complete=True is delayed or the conversation resets.
    if (
        update.get("preferred_doctor")
        and not state.get("preferred_doctor")
        and state.get("user_db_id")
    ):
        try:
            await upsert_user(state["phone"], {
                "doctor_id": DOCTOR_IDS.get(update["preferred_doctor"])
            }, user_id=state["user_db_id"])
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("Failed to persist preferred_doctor early")

    # Calculate age automatically from birth_date
    birth_date_str = update.get("birth_date") or state.get("birth_date")
    if birth_date_str and not state.get("patient_age"):
        try:
            bd = datetime.strptime(birth_date_str, "%d/%m/%Y")
            today = datetime.now()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
            update["patient_age"] = age
        except ValueError:
            pass

    # Merge collected fields with existing state for the upsert (used both for
    # is_complete check and for the actual upsert below).
    merged = {**state, **{k: v for k, v in update.items() if k not in ("messages", "stage")}}

    # Map merged state to the dict shape expected by is_registration_complete.
    _reg_check = _registration_state_to_user(merged)
    _missing = missing_registration_field(_reg_check)

    # Desempate LLM × gate determinístico. A LLM diz "tudo anotado!", o gate diz que
    # falta um campo — e o _route_entry então devolve TODO turno seguinte para o
    # collect_info, um nó sem ferramentas. Ninguém volta a perguntar, e a conversa
    # trava para sempre: comprovante, agendamento e documento param de funcionar
    # (caso Bernardo Lima Beltrão Teixeira, 5581987415206, 31/07/2026 —
    # guardian_relationship vazio porque "Para meu filho" não diz mãe ou pai).
    # Quem manda é o gate: substituímos a despedida pela pergunta que falta.
    if result.is_complete and not birth_date_invalid and _missing:
        _forced_q = _registration_question(_missing, merged)
        if _forced_q:
            import logging as _log
            _log.getLogger(__name__).warning(
                "COLLECT_INFO_INCOMPLETE: LLM marcou is_complete=true faltando %s phone=%s — "
                "resposta substituída pela pergunta determinística",
                _missing, state["phone"],
            )
            reply = _forced_q

    _complete = result.is_complete and not birth_date_invalid and not _missing

    # O lado espelhado do desempate acima. Aqui a LLM diz is_complete=false, o gate
    # diz que não falta nada e a resposta dela não pergunta coisa alguma: ela agradece
    # o último dado ("Entendi, obrigada por compartilhar.") e cala. O stage fica em
    # collect_info, o _route_after_collect manda o turno para o END e o agendamento
    # nunca começa — a conversa só volta a andar quando uma atendente injeta uma
    # instrução na mão (caso Marcelo Brayner Filho, 5581999865181, 04/08/2026;
    # a LLM tinha esquecido de emendar a pergunta do profissional que encaminhou).
    # Sem pergunta pendente e sem campo faltando, quem manda é o gate.
    # A rede só vale quando o turno de fato coletou um campo novo: assim uma
    # despedida legítima com o cadastro já pronto ("fico à disposição!") continua
    # encerrando o turno sem a Eva emendar outra mensagem por conta própria.
    if not _complete and not birth_date_invalid and not _missing and "?" not in reply:
        _collected_new_field = any(
            state.get(k) != v for k, v in update.items() if k not in ("messages", "stage")
        )
        if _collected_new_field:
            import logging as _log
            _log.getLogger(__name__).warning(
                "COLLECT_INFO_STALLED: LLM marcou is_complete=false sem perguntar nada "
                "e sem campo faltando phone=%s — cadastro dado por completo",
                state["phone"],
            )
            _complete = True

    if _complete:
        update["stage"] = "patient_agent"
        if _is_document:
            update["pending_action"] = "request_document"
        try:
            _final_id = await upsert_user(state["phone"], {
                "name": merged.get("user_name"),
                "patient_name": merged.get("patient_name"),
                "age": merged.get("patient_age"),
                "birth_date": merged.get("birth_date"),
                "patient_cpf": merged.get("patient_cpf"),
                "guardian_name": merged.get("guardian_name"),
                "guardian_cpf": merged.get("guardian_cpf"),
                "guardian_relationship": merged.get("guardian_relationship"),
                "is_patient": merged.get("is_patient"),
                "is_returning_patient": merged.get("is_returning_patient"),
                "doctor_id": DOCTOR_IDS.get(merged.get("preferred_doctor", ""), None),
                "email": merged.get("patient_email"),
                "consultation_reason": merged.get("consultation_reason"),
                "referral_professional": merged.get("referral_professional"),
                "active": True,
            }, user_id=state.get("user_db_id"))
            # A reconciliação de retornante pode devolver um id diferente do
            # enviado (cadastro existente sob outro telefone) — ver _extract_and_ask.
            if _final_id and _final_id != state.get("user_db_id"):
                update["user_db_id"] = _final_id
            await log_event("info_collected", state["phone"], {
                "patient_name": merged.get("patient_name"),
                "patient_age": merged.get("patient_age"),
                "is_patient": merged.get("is_patient"),
                "preferred_doctor": merged.get("preferred_doctor"),
            })
        except Exception:
            import logging as _log
            _log.getLogger(__name__).exception("Failed to upsert user after collect_info")
    else:
        # Os passos programáticos gravam os campos que coletam a cada turno (ver
        # _STATE_TO_DB em _extract_and_ask), mas o motivo da consulta e o
        # profissional que encaminhou são colhidos pela LLM e só chegavam ao banco
        # no bloco acima, quando o cadastro fecha. Se o turno não fecha o cadastro,
        # eles ficavam apenas no checkpoint do LangGraph: o Marcelo Brayner Filho
        # (5581999865181, 04/08/2026) teve a consulta marcada e paga com o
        # prontuário sem o motivo, e a clínica preencheu à mão.
        # Só os campos novos deste turno entram no payload — mandar os outros como
        # None sobrescreveria dado bom que já está gravado. E só quando o valor
        # mudou: a LLM re-extrai esses campos do histórico a cada turno, e sem
        # essa checagem cada turno viraria uma escrita repetida do mesmo valor.
        _incremental = {
            db_key: update[state_key]
            for state_key, db_key in (
                ("consultation_reason", "consultation_reason"),
                ("referral_professional", "referral_professional"),
            )
            if update.get(state_key) is not None
            and update[state_key] != state.get(state_key)
        }
        if _incremental:
            try:
                _returned_id = await upsert_user(
                    state["phone"], _incremental, user_id=state.get("user_db_id")
                )
                if _returned_id and _returned_id != state.get("user_db_id"):
                    update["user_db_id"] = _returned_id
            except Exception:
                import logging as _log
                _log.getLogger(__name__).exception(
                    "Failed to persist partial LLM-collected fields in collect_info"
                )

    await send_text(state["phone"], reply)
    await save_message(state["phone"], "assistant", reply)
    update["messages"] = [AIMessage(content=reply)]

    return update


def _extract_pending_appointment(text: str, state: dict) -> dict | None:
    """Parse the confirmation summary Eva sends and return structured appointment data.

    Summary format:
        Só confirmar antes de registrar: 😊
        📅 Terça-feira, dia 16/06, às 17:00
        👨‍⚕️ Dr. Júlio
        👤 Paciente: Paula Muniz
        📍 Modalidade: Presencial
    """
    import re as _re
    _CONFIRM_SUMMARY_MARKER = "Só confirmar antes de registrar"
    import re as _re_csa
    if not _re_csa.search(r'Só\s+(?:para\s+)?confirmar\s+antes\s+de\s+registrar', text):
        return None

    # Date + time
    date_match = _re.search(r'dia\s+(\d{1,2}/\d{2}).*?às\s+(\d{2}:\d{2})', text, _re.DOTALL)
    if not date_match:
        return None
    date_str = date_match.group(1)   # "16/06"
    time_str = date_match.group(2)   # "17:00"

    from datetime import date as _date
    from zoneinfo import ZoneInfo as _ZI
    _TZ_rec = _ZI("America/Recife")
    today = datetime.now(_TZ_rec).date()
    try:
        day, month = map(int, date_str.split('/'))
        year = today.year
        candidate = _date(year, month, day)
        if candidate < today:
            candidate = _date(year + 1, month, day)
    except (ValueError, OverflowError):
        return None

    slot_datetime = f"{candidate.isoformat()}T{time_str}:00"

    # Doctor
    if "Dr. Júlio" in text or "Dr. Julio" in text:
        doctor = "julio"
    elif "Dra. Bruna" in text:
        doctor = "bruna"
    else:
        doctor = state.get("preferred_doctor") or ""

    # Modality
    mod_match = _re.search(r'Modalidade[:\s]+(\w+)', text)
    modality = ""
    if mod_match:
        mod_raw = mod_match.group(1).lower()
        if mod_raw in ("presencial", "online"):
            modality = mod_raw

    # Duration
    p_age = state.get("patient_age") or 99
    is_returning = state.get("is_returning_patient")
    duration = 120 if (p_age < 18 and not is_returning and doctor == "julio") else 60

    return {
        "slot_datetime": slot_datetime,
        "slot_duration_minutes": duration,
        "modality": modality,
        "doctor": doctor,
    }


def _offered_hms_from_messages(messages: list) -> set[str]:
    """Horários (HH:MM) da oferta mais recente do get_available_slots no histórico.

    Procura a ToolMessage mais recente do get_available_slots; se o nome não estiver
    preenchido, cai para a ToolMessage mais recente cujo conteúdo tenha horários.
    Set vazio quando não há oferta rastreável (encaixe, instrução da atendente)."""
    import re as _re
    _time = _re.compile(r'\b(\d{1,2}):(\d{2})\b')

    def _hms(text: str) -> set[str]:
        out = set()
        for _m in _time.finditer(text or ""):
            h, mi = int(_m.group(1)), int(_m.group(2))
            if 6 <= h <= 21:
                out.add(f"{h:02d}:{mi:02d}")
        return out

    named = None
    any_tool = None
    for _m in reversed(list(messages)):
        if getattr(_m, "type", "") != "tool":
            continue
        _content = str(getattr(_m, "content", "") or "")
        if getattr(_m, "name", None) == "get_available_slots":
            named = _content
            break
        if any_tool is None and _time.search(_content):
            any_tool = _content
    source = named if named is not None else any_tool
    return _hms(source) if source else set()


def _requested_hms_from_messages(messages: list, limit: int = 3) -> set[str]:
    """Horários que o paciente pediu explicitamente nas últimas mensagens humanas.

    Reconhece "11h", "11 h", "11hs", "11:30", "às 11". Ignora datas (dd/mm) porque
    o padrão de hora exige o marcador de hora (h/:/às)."""
    import re as _re
    _hm = _re.compile(r'\b(\d{1,2}):(\d{2})\b')
    _hs = _re.compile(r'\b(\d{1,2})\s*h(?:oras|s)?\b', _re.IGNORECASE)
    _as = _re.compile(r'\b[àa]s\s+(\d{1,2})\b', _re.IGNORECASE)
    out: set[str] = set()
    seen = 0
    for _m in reversed(list(messages)):
        if getattr(_m, "type", "") != "human":
            continue
        seen += 1
        text = str(getattr(_m, "content", "") or "")
        for mt in _hm.finditer(text):
            h, mi = int(mt.group(1)), int(mt.group(2))
            if 6 <= h <= 21:
                out.add(f"{h:02d}:{mi:02d}")
        for rgx in (_hs, _as):
            for mt in rgx.finditer(text):
                h = int(mt.group(1))
                if 6 <= h <= 21:
                    out.add(f"{h:02d}:00")
        if seen >= limit:
            break
    return out


def _correct_confirmation_summary_time(content: str, state: dict) -> str | None:
    """Corrige o horário do resumo "Só confirmar antes de registrar" quando o modelo
    escreve um horário que não foi oferecido e diverge do que o paciente pediu.

    O gpt-5.6-luna (reasoning_effort="none") passou a chutar 08:00 (primeiro slot da
    grade) no texto do resumo, mesmo com o paciente tendo escolhido outro horário.
    Só corrige com evidência confiável: o horário-alvo é a interseção ÚNICA entre o
    que o paciente pediu e o que foi realmente ofertado. Sem isso, não altera nada.

    Retorna o texto corrigido, ou None quando não há o que corrigir. Casos reais em
    31/08/2026 (João Pedro 5581991542212, Paula/Glória 5581991270824) — ver
    docs/incidentes/2026-08-31-horario-errado-na-confirmacao-luna.md."""
    import re as _re
    if not _re.search(r'Só\s+(?:para\s+)?confirmar\s+antes\s+de\s+registrar', content or ""):
        return None
    _m = _re.search(r'[àa]s\s+(\d{1,2}):(\d{2})', content)
    if not _m:
        return None
    summary_hm = f"{int(_m.group(1)):02d}:{int(_m.group(2)):02d}"

    messages = state.get("messages") or []
    offered = _offered_hms_from_messages(messages)
    if not offered:
        return None  # sem oferta rastreável — não mexe (encaixe, atendente)

    requested = _requested_hms_from_messages(messages)

    # Já consistente: horário do resumo foi ofertado e não contradiz o pedido.
    if summary_hm in offered and (not requested or summary_hm in requested):
        return None

    intended = sorted(hm for hm in requested if hm in offered)
    if len(intended) != 1:
        return None  # sem alvo único e confiável
    target = intended[0]
    if target == summary_hm:
        return None

    corrected = _re.sub(
        r'([àa]s\s+)\d{1,2}:\d{2}', rf'\g<1>{target}', content, count=1
    )
    return corrected if corrected != content else None


def _duration_rule_for(state: dict, patient_age: int, first_name: str) -> str:
    """Escolhe a regra de duração/perfil injetada no prompt do patient_agent.

    A idade é o ÚNICO critério para menor de idade. Ter responsável na conversa
    ou ser primeira consulta não torna o paciente menor — ver ADULT_RULE (caso
    Beatriz, 20 anos, 04/08/2026)."""
    is_minor = patient_age < 18
    is_minor_first = (
        is_minor
        and not state.get("is_returning_patient", False)
        and state.get("preferred_doctor") == "julio"
    )
    if is_minor_first:
        if state.get("minor_first_consult_explained"):
            return MINOR_RULE_SCHEDULING_ONLY.format(patient_name=first_name, patient_age=patient_age)
        return MINOR_RULE.format(patient_name=first_name, patient_age=patient_age)
    if is_minor:
        return MINOR_RETURNING_RULE.format(patient_age=patient_age)
    return ADULT_RULE


async def patient_agent_node(state: ConversationState, config: RunnableConfig) -> dict:
    """
    Single LLM call per turn. If the LLM returns tool calls, the graph routes
    to the ToolNode. When it returns plain text, it sends to WhatsApp and ends.
    """
    import logging as _log_pa
    _pa_logger = _log_pa.getLogger(__name__)

    # ── Check for Débora insistence → transfer to human ──────────────────────
    if _detect_debora_insistence(state.get("messages", [])):
        _pa_logger.info("DEBORA_INSISTENCE detected for %s — transferring to human", state.get("phone"))
        await log_event("debora_insistence_detected", state.get("phone"))
        from app.graph.tools import transfer_to_human as _transfer_tool
        result = await _transfer_tool.coroutine(
            reason="Paciente insistindo em falar com Débora",
            state=state,
            config=config,
        )
        return {"messages": [AIMessage(content=result)]}

    # ── Guard: resultado slot-taken de register_payment vai ao paciente verbatim ─
    # Comprovante de consulta cancelada cujo horário original já foi ocupado:
    # register_payment marca pending_reschedule e devolve a mensagem dizendo que o
    # horário não está mais disponível (taxa preservada). Deixar a LLM re-sintetizar
    # esse resultado já produziu o OPOSTO — "Vou seguir com a reativação da consulta
    # para o dia 13/08, às 14:00... conforme combinado" (caso Ricardo José Vieira
    # Cunha Filho, contato 5581988912861, 10/08/2026): o contexto da conversa pesa
    # mais que a ToolMessage. Sobre disponibilidade a tool é a única fonte de
    # verdade, então o texto dela vai direto ao paciente e o turno termina aqui.
    from app.graph.tools import REACTIVATION_SLOT_TAKEN_MARKER as _SLOT_TAKEN_MARKER
    _msgs_rp = list(state.get("messages") or [])
    _trailing_tools = []
    for _m_rp in reversed(_msgs_rp):
        if getattr(_m_rp, "type", "") == "tool":
            _trailing_tools.append(_m_rp)
        else:
            break
    if _trailing_tools:
        # Nome da tool: o ToolNode preenche .name; fallback via tool_calls da
        # AIMessage imediatamente anterior ao bloco de ToolMessages.
        _prev_ai_rp = _msgs_rp[-len(_trailing_tools) - 1] if len(_msgs_rp) > len(_trailing_tools) else None
        _tc_names_rp = {
            tc.get("id"): tc.get("name")
            for tc in (getattr(_prev_ai_rp, "tool_calls", None) or [])
        }
        for _tm_rp in _trailing_tools:
            _tm_name = getattr(_tm_rp, "name", None) or _tc_names_rp.get(getattr(_tm_rp, "tool_call_id", None))
            _tm_content = str(getattr(_tm_rp, "content", "") or "")
            if _tm_name == "register_payment" and _SLOT_TAKEN_MARKER in _tm_content:
                _pa_logger.info(
                    "GUARD_SLOT_TAKEN_VERBATIM: sending register_payment slot-taken result "
                    "directly to patient phone=%s", state.get("phone"),
                )
                await send_text(state["phone"], _tm_content)
                await save_message(state["phone"], "assistant", _tm_content)
                # pending_appointment antigo não pode sobreviver: um "sim" na próxima
                # mensagem confirmaria programaticamente um horário que acabou de se
                # provar indisponível.
                return {
                    "messages": [AIMessage(content=_tm_content)],
                    "pending_appointment": None,
                    "silent_mode": False,
                }

    # ── DB ↔ checkpoint hydration ─────────────────────────────────────────────
    # Hydrates missing fields from DB using the new patients/contacts schema.
    # Only runs when at least one field is absent — once set, the checkpoint is trusted.
    # Sources: contacts.name → user_name, patients.* → patient_name/preferred_doctor/
    # is_returning_patient/patient_email, patient_contacts.is_self → is_patient.
    _sync_updates: dict = {}
    _user_db_id = state.get("user_db_id")

    _needs_hydration = (
        not _user_db_id
        or not state.get("user_name")
        or not state.get("patient_name")
        or state.get("is_patient") is None
        or state.get("is_returning_patient") is None
    )

    if _needs_hydration and state.get("phone"):
        try:
            from app.patients import resolve_active_patient as _resolve_active
            from app.database import get_supabase as _get_db_hydr
            _ctx = await _resolve_active(state["phone"])
            _h_contact = _ctx.get("contact")
            _h_patient = _ctx.get("patient")

            if _h_contact and not state.get("user_name"):
                _sync_updates["user_name"] = _h_contact["name"]

            if _h_patient:
                if not _user_db_id:
                    _user_db_id = _h_patient["id"]
                    _sync_updates["user_db_id"] = _h_patient["id"]
                if not state.get("patient_name"):
                    _sync_updates["patient_name"] = _h_patient["name"]
                if not state.get("preferred_doctor") and _h_patient.get("doctor_id"):
                    _sync_updates["preferred_doctor"] = DOCTOR_NAMES.get(_h_patient["doctor_id"])
                if not state.get("patient_email") and _h_patient.get("email"):
                    _sync_updates["patient_email"] = _h_patient["email"]
                if state.get("is_returning_patient") is None and _h_patient.get("is_returning_patient") is not None:
                    _sync_updates["is_returning_patient"] = _h_patient["is_returning_patient"]
                if not state.get("financial_name") and _h_patient.get("financial_name"):
                    _sync_updates["financial_name"] = _h_patient["financial_name"]
                if not state.get("financial_cpf") and _h_patient.get("financial_cpf"):
                    _sync_updates["financial_cpf"] = _h_patient["financial_cpf"]
                if not state.get("financial_email") and _h_patient.get("financial_email"):
                    _sync_updates["financial_email"] = _h_patient["financial_email"]
                if not state.get("social_name") and _h_patient.get("social_name"):
                    _sync_updates["social_name"] = _h_patient["social_name"]

            if state.get("is_patient") is None and _h_contact and _h_patient:
                _db_h = await _get_db_hydr()
                _pc_h = await _db_h.from_("patient_contacts") \
                    .select("is_self") \
                    .eq("patient_id", _h_patient["id"]) \
                    .eq("contact_id", _h_contact["id"]) \
                    .maybe_single() \
                    .execute()
                if _pc_h and _pc_h.data:
                    _sync_updates["is_patient"] = bool(_pc_h.data.get("is_self"))
                    # Mark as unconfirmed — came from DB, not from an explicit answer in this conversation.
                    _sync_updates["_is_patient_confirmed"] = False

            if _sync_updates:
                _pa_logger.info("DB hydration for %s: %s", state["phone"], list(_sync_updates.keys()))
        except Exception:
            _pa_logger.exception("DB hydration failed for %s", state.get("phone"))

    # Lightweight financial-fields hydration — independent of the main hydration
    # block above (which only runs when core registration fields are missing).
    # Without this, a returning patient whose financial_name/cpf/email were already
    # saved on a prior NF request would have Eva ask for them again every time,
    # since those fields live only in the DB once the main hydration has already run.
    _fin_user_db_id = _sync_updates.get("user_db_id") or _user_db_id
    if _fin_user_db_id and not (state.get("financial_name") and state.get("financial_cpf") and state.get("financial_email")):
        try:
            from app.database import get_supabase as _get_db_fin
            _db_fin = await _get_db_fin()
            _fin_r = await _db_fin.from_("patients").select("financial_name, financial_cpf, financial_email") \
                .eq("id", _fin_user_db_id).maybe_single().execute()
            if _fin_r and _fin_r.data:
                if not state.get("financial_name") and _fin_r.data.get("financial_name"):
                    _sync_updates["financial_name"] = _fin_r.data["financial_name"]
                if not state.get("financial_cpf") and _fin_r.data.get("financial_cpf"):
                    _sync_updates["financial_cpf"] = _fin_r.data["financial_cpf"]
                if not state.get("financial_email") and _fin_r.data.get("financial_email"):
                    _sync_updates["financial_email"] = _fin_r.data["financial_email"]
        except Exception:
            _pa_logger.exception("Financial-fields hydration failed for %s", state.get("phone"))

    # Apply sync updates to local state view so this turn uses the correct values
    if _sync_updates:
        state = {**state, **_sync_updates}

    # ── Programmatic confirmation bypass ─────────────────────────────────────
    # If Eva already sent the confirmation summary and the patient is now confirming,
    # call confirm_appointment DIRECTLY without going through the LLM.
    # This prevents: (a) LLM ignoring the guard and re-fetching slots,
    #                (b) double-booking when patient sends a non-standard affirmative
    #                    that the LLM interprets as confirmation without the guard.
    # Palavras-chave afirmativas para confirmar pending_appointment.
    # IMPORTANTE: a verificação usa word-boundary (re.search) para evitar falsos
    # positivos — ex: "ta" não deve bater em "quinta" ou "semana".
    _PENDING_AFFIRMATIVE = {
        "sim", "pode", "confirma", "confirmo", "ok", "isso", "pode ser",
        "tá", "ta", "tá bom", "ta bom", "perfeito", "ótimo", "otimo",
        "certo", "claro", "exato", "👍", "✅", "pode confirmar",
        "confirmar", "quero", "quero confirmar", "vai", "bora", "fechado",
    }
    # Palavras-chave negativas — se presentes, NÃO confirmar mesmo que haja
    # alguma palavra afirmativa na mesma mensagem.
    _PENDING_NEGATIVE = {
        "não", "nao", "cancela", "cancelar", "desculpe", "errado", "errei",
        "outro", "outra", "diferente", "mudei", "prefiro outro", "não quero",
    }
    _pending_appt = state.get("pending_appointment")
    if _pending_appt:
        import re as _re
        _last_msg = ""
        for _m in reversed(list(state["messages"])):
            if getattr(_m, "type", "") == "human":
                _last_msg = (getattr(_m, "content", "") or "").strip().lower()
                break

        def _word_match(keyword: str, text: str) -> bool:
            """True se keyword aparece como palavra inteira em text."""
            return bool(_re.search(r'(?<!\w)' + _re.escape(keyword) + r'(?!\w)', text))

        _has_negative = any(_word_match(w, _last_msg) for w in _PENDING_NEGATIVE)
        _has_affirmative = any(_word_match(w, _last_msg) for w in _PENDING_AFFIRMATIVE)

        if _last_msg and _has_affirmative and not _has_negative:
            _pa_logger.info("PENDING_APPT_CONFIRM slot=%s modality=%s", _pending_appt.get("slot_datetime"), _pending_appt.get("modality"))
            from app.graph.tools import confirm_appointment as _confirm_tool
            from app.whatsapp import send_text as _send_text
            from app.database import save_message as _save_msg

            # ── Remarcação em andamento ───────────────────────────────────────
            # Quando a consulta está em pending_reschedule, este "sim" confirma o
            # NOVO horário de uma consulta que já existe e já teve taxa paga — não
            # é um agendamento novo. Chamar confirm_appointment aqui bate sempre na
            # guarda de agendamento duplicado (o paciente, por definição, já tem
            # consulta), e a remarcação só andava depois de uma volta inteira pela
            # LLM — volta que nem acontecia até a correção do roteamento
            # (caso Elisabete/Isaac, 5581987385089, 02/08/2026).
            # Só desviamos com UMA remarcação em aberto: com mais de uma não dá
            # para saber qual consulta o "sim" está movendo, então deixamos a
            # guarda de confirm_appointment devolver os IDs para a LLM decidir.
            _resched_appt_id = None
            try:
                from app.database import get_supabase as _get_sb_r
                _pid_r = state.get("user_db_id")
                if _pid_r:
                    _sb_r = await _get_sb_r()
                    _r_rows = await _sb_r.from_("appointments").select(
                        "appointment_id"
                    ).eq("patient_id", _pid_r).eq("status", "pending_reschedule").execute()
                    _r_data = _r_rows.data or []
                    if len(_r_data) == 1:
                        _resched_appt_id = _r_data[0]["appointment_id"]
                    elif len(_r_data) > 1:
                        _pa_logger.warning(
                            "PENDING_APPT_CONFIRM %d remarcações em aberto — deixando a guarda decidir phone=%s",
                            len(_r_data), state.get("phone"),
                        )
            except Exception:
                _pa_logger.exception("PENDING_APPT_CONFIRM pending_reschedule lookup failed")

            if _resched_appt_id:
                _pa_logger.info(
                    "PENDING_APPT_RESCHEDULE appt=%s slot=%s", _resched_appt_id, _pending_appt.get("slot_datetime")
                )
                from app.graph.tools import reschedule_appointment as _resched_tool
                try:
                    _result = await _resched_tool.coroutine(
                        appointment_id=_resched_appt_id,
                        new_slot_datetime=_pending_appt["slot_datetime"],
                        slot_duration_minutes=_pending_appt["slot_duration_minutes"],
                        modality=_pending_appt.get("modality", ""),
                        state=state,
                        config=config,
                    )
                except Exception:
                    _pa_logger.exception("PENDING_APPT_RESCHEDULE failed")
                    _result = "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] Falha ao remarcar a consulta."

                if _result.startswith("[INSTRUÇÃO INTERNA"):
                    # Horário ocupado no meio do caminho, dia sem atendimento, etc.
                    # Devolve à LLM (get_available_slots) em vez de enviar ao paciente.
                    _pa_logger.warning("PENDING_APPT_RESCHEDULE internal instruction: %.200s", _result)
                    from langchain_core.messages import AIMessage as _AIR, ToolMessage as _TMR
                    import uuid as _uuid_r
                    _tc_r = str(_uuid_r.uuid4())
                    _ai_r = _AIR(content="", tool_calls=[{"name": "reschedule_appointment", "args": {}, "id": _tc_r, "type": "tool_use"}])
                    _tm_r = _TMR(
                        content=_result,
                        tool_call_id=_tc_r,
                        additional_kwargs={RESUME_AFTER_TOOL: True},
                    )
                    return {"pending_appointment": None, "messages": [_ai_r, _tm_r], "silent_mode": False, **_sync_updates}

                await _send_text(state["phone"], _result)
                await _save_msg(state["phone"], "assistant", _result)
                from langchain_core.messages import AIMessage as _AIR2, ToolMessage as _TMR2
                import uuid as _uuid_r2
                _tc_r2 = str(_uuid_r2.uuid4())
                _ai_r2 = _AIR2(content="", tool_calls=[{"name": "reschedule_appointment", "args": {}, "id": _tc_r2, "type": "tool_use"}])
                _tm_r2 = _TMR2(content=_result, tool_call_id=_tc_r2)
                return {
                    "pending_appointment": None,
                    "messages": [_ai_r2, _tm_r2, _AIR2(content=_result)],
                    "silent_mode": False,
                    **_sync_updates,
                }

            try:
                _result = await _confirm_tool.coroutine(
                    slot_datetime=_pending_appt["slot_datetime"],
                    slot_duration_minutes=_pending_appt["slot_duration_minutes"],
                    modality=_pending_appt.get("modality", ""),
                    session_note="",
                    force_encaixe=False,
                    patient_name_override="",
                    state=state,
                    config=config,
                )
            except Exception:
                _pa_logger.exception("PENDING_APPT_CONFIRM failed")
                _result = "Erro ao confirmar o agendamento. Tente novamente."

            # Convert semantic codes to patient-friendly messages
            from app.utils import display_name as _dn
            _contact_name = _dn(state.get("user_name") or "") or "você"
            # confirm_appointment success codes may be prefixed with the internal-instruction tag.
            # Strip it before matching, otherwise a successful booking is misread as an error.
            _INT_PREFIX = "[INSTRUÇÃO INTERNA — NÃO ENVIE AO PACIENTE] "
            _result_body = _result[len(_INT_PREFIX):] if _result.startswith(_INT_PREFIX) else _result
            if _result_body.startswith("AGENDAMENTO_OK\n") or _result_body.startswith("AGENDAMENTO_TAXA_DISPENSADA\n") or _result_body.startswith("AGENDAMENTO_CORTESIA\n"):
                # Extract doctor/date/time line (2nd line)
                _lines = _result_body.splitlines()
                _appt_line = _lines[1] if len(_lines) > 1 else ""
                if _result_body.startswith("AGENDAMENTO_CORTESIA\n"):
                    _patient_msg = (
                        f"Perfeito, {_contact_name}! 😊 Consulta confirmada:\n{_appt_line}\n\n"
                        f"Como combinado, a taxa de reserva está isenta. Até lá!"
                    )
                elif _result_body.startswith("AGENDAMENTO_TAXA_DISPENSADA\n"):
                    _patient_msg = (
                        f"Perfeito, {_contact_name}! 😊 Consulta confirmada:\n{_appt_line}\n\n"
                        f"A taxa de reserva foi dispensada. Até lá!"
                    )
                else:
                    _PIX_KEY = CORRECT_PIX_KEY
                    _patient_msg = (
                        f"Consulta registrada! ✅\n{_appt_line}\n\n"
                        f"Para garantir a vaga, é necessário o pagamento da taxa de reserva de R$ 100,00 em até 2 horas.\n"
                        f"💳 PIX: {_PIX_KEY}\n\n"
                        f"Esse valor será abatido do total da consulta. Em caso de cancelamento ou remarcação com menos de 24h de antecedência ou ausência sem justificativa, a taxa não é devolvida."
                    )
            elif _result.startswith("[INSTRUÇÃO INTERNA"):
                _pa_logger.warning("PENDING_APPT_CONFIRM internal error phone=%s result=%.200s", state.get("phone"), _result_body)
                # Before flagging an error, check if patient already has this appointment
                # scheduled (bypass fired twice — e.g. patient said "Ok" after PIX message).
                _already_booked = False
                try:
                    from app.database import get_supabase as _get_sb
                    _sb = await _get_sb()
                    from app.database import get_users_by_phone as _get_users
                    _users = await _get_users(state["phone"])
                    _uids = [u["id"] for u in _users]
                    if _uids:
                        _slot_dt_check = _pending_appt.get("slot_datetime", "")
                        _dup_check = await _sb.from_("appointments").select("appointment_id, start_time").in_("patient_id", _uids).eq("status", "scheduled").execute()
                        from datetime import datetime as _dtt
                        from zoneinfo import ZoneInfo as _ZI
                        _slot_parsed = _dtt.fromisoformat(_slot_dt_check).astimezone(_ZI("America/Recife")) if _slot_dt_check else None
                        for _row in (_dup_check.data or []):
                            _row_dt = _dtt.fromisoformat(_row["start_time"]).astimezone(_ZI("America/Recife"))
                            if _slot_parsed and abs((_row_dt - _slot_parsed).total_seconds()) < 60:
                                _already_booked = True
                                break
                except Exception:
                    pass
                if _already_booked:
                    _doctor_lbl2 = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_pending_appt.get("doctor", ""), "médico(a)")
                    _patient_msg = f"Sua consulta já está confirmada com {_doctor_lbl2}! 😊 Qualquer dúvida, estou à disposição."
                elif "acabou de ser ocupado" in _result_body or "acabou de ser preenchido" in _result_body:
                    # Slot taken between offer and confirmation — clear pending and ask Eva to find alternatives
                    _slot_str = ""
                    try:
                        from datetime import datetime as _dtt2
                        from zoneinfo import ZoneInfo as _ZI2
                        _sd = _pending_appt.get("slot_datetime", "")
                        if _sd:
                            _dt = _dtt2.fromisoformat(_sd).astimezone(_ZI2("America/Recife"))
                            _slot_str = f" das {_dt.strftime('%H:%M')} do dia {_dt.strftime('%d/%m')}"
                    except Exception:
                        pass
                    _patient_msg = (
                        f"Que pena, {_contact_name}! O horário{_slot_str} acabou de ser preenchido por outra pessoa. "
                        f"Vou buscar as próximas opções disponíveis para você! 😊"
                    )
                    # Inject tool result so LLM picks up context and calls get_available_slots
                    await _send_text(state["phone"], _patient_msg)
                    await _save_msg(state["phone"], "assistant", _patient_msg)
                    from langchain_core.messages import AIMessage as _AI2, ToolMessage as _TM2
                    import uuid as _uuid2
                    _tc2 = str(_uuid2.uuid4())
                    _ai2 = _AI2(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc2, "type": "tool_use"}])
                    _tm2 = _TM2(
                        content=_result_body + "\nBusque novos horários automaticamente com get_available_slots.",
                        tool_call_id=_tc2,
                        additional_kwargs={RESUME_AFTER_TOOL: True},
                    )
                    return {"pending_appointment": None, "messages": [_ai2, _tm2], "silent_mode": False, **_sync_updates}
                elif "já tem consulta" in _result_body or "use mark_reschedule_in_progress" in _result_body:
                    # Guard fired: patient already has a scheduled appointment.
                    # Inject the internal instruction as a ToolMessage so the LLM
                    # follows up with mark_reschedule_in_progress → reschedule_appointment.
                    from langchain_core.messages import AIMessage as _AI3, ToolMessage as _TM3
                    import uuid as _uuid3
                    _tc3 = str(_uuid3.uuid4())
                    _ai3 = _AI3(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc3, "type": "tool_use"}])
                    _tm3 = _TM3(
                        content=_result_body,
                        tool_call_id=_tc3,
                        additional_kwargs={RESUME_AFTER_TOOL: True},
                    )
                    return {"pending_appointment": None, "messages": [_ai3, _tm3], "silent_mode": False, **_sync_updates}
                elif "get_available_slots" in _result_body:
                    # O horário não é agendável e a própria instrução interna diz o
                    # caminho: avisar o paciente e buscar outros horários. Cobre dia
                    # bloqueado, horário fora da grade/da exceção do dia e bloco que
                    # não cabe no expediente — hoje e qualquer recusa futura que peça
                    # get_available_slots. Antes disso só "acabou de ser ocupado" e
                    # "já tem consulta" eram reconhecidos, e o resto caía no ramo de
                    # erro inesperado: a paciente levava "Tive um problema ao confirmar
                    # o agendamento" + handoff em vez de novas opções (caso Geórgia,
                    # 5583998264807, 17/08/2026 — 19/08 às 16h com a Dra. Bruna foi
                    # ofertado de manhã e bloqueado na grade antes de ela responder).
                    _pa_logger.warning(
                        "PENDING_APPT_CONFIRM slot indisponível — devolvendo à LLM phone=%s result=%.200s",
                        state.get("phone"), _result_body,
                    )
                    from langchain_core.messages import AIMessage as _AI5, ToolMessage as _TM5
                    import uuid as _uuid5
                    _tc5 = str(_uuid5.uuid4())
                    _ai5 = _AI5(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc5, "type": "tool_use"}])
                    _tm5 = _TM5(
                        content=_result_body,
                        tool_call_id=_tc5,
                        additional_kwargs={RESUME_AFTER_TOOL: True},
                    )
                    return {"pending_appointment": None, "messages": [_ai5, _tm5], "silent_mode": False, **_sync_updates}
                else:
                    # Unexpected error — notify attendant and transfer.
                    _pa_logger.error("PENDING_APPT_CONFIRM unexpected error phone=%s result=%.300s", state.get("phone"), _result_body)
                    _patient_msg = f"Ops, {_contact_name}! Tive um problema ao confirmar o agendamento. Vou te passar para nossa equipe agora. 😊"
                    await _send_text(state["phone"], _patient_msg)
                    await _save_msg(state["phone"], "assistant", _patient_msg)
                    try:
                        from app.graph.tools import transfer_to_human as _tth
                        _fake_config = {"configurable": {"thread_id": state["phone"], "phone": state["phone"]}}
                        await _tth.coroutine(
                            reason=f"Erro ao confirmar agendamento: {_result_body[:200]}",
                            state=state,
                            config=_fake_config,
                        )
                    except Exception as _tth_exc:
                        _pa_logger.error("PENDING_APPT_CONFIRM transfer_to_human failed: %s", _tth_exc)
                    from langchain_core.messages import AIMessage as _AI4, ToolMessage as _TM4
                    import uuid as _uuid4
                    _tc4 = str(_uuid4.uuid4())
                    _ai4 = _AI4(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc4, "type": "tool_use"}])
                    _tm4 = _TM4(content=_result_body, tool_call_id=_tc4)
                    return {"pending_appointment": None, "messages": [_ai4, _tm4, _AI4(content=_patient_msg)], "silent_mode": False, **_sync_updates}
            else:
                _patient_msg = _result

            await _send_text(state["phone"], _patient_msg)
            await _save_msg(state["phone"], "assistant", _patient_msg)
            from langchain_core.messages import AIMessage as _AI, ToolMessage as _TM
            import uuid as _uuid_pa
            _tc_id_pa = str(_uuid_pa.uuid4())
            _ai_with_tool = _AI(content="", tool_calls=[{"name": "confirm_appointment", "args": {}, "id": _tc_id_pa, "type": "tool_use"}])
            _tool_msg = _TM(content=_result, tool_call_id=_tc_id_pa)
            return {"pending_appointment": None, "messages": [_ai_with_tool, _tool_msg, _AI(content=_patient_msg)], "silent_mode": False, **_sync_updates}

    doctor_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(
        state.get("preferred_doctor", ""), "médico(a)"
    )
    _raw_age = state.get("patient_age")
    patient_age = _raw_age or 99          # numeric fallback for logic checks
    patient_age_display = f"{patient_age} anos" if _raw_age else "não informada"
    _full_name = state.get("social_name") or state.get("patient_name") or state.get("user_name") or "paciente"
    from app.utils import display_name as _dn
    first_name = _dn(_full_name)
    # contact_name: who is on WhatsApp. May differ from patient_name (e.g. guardian).
    # When is_patient is explicitly False the contact is NOT the patient; avoid using
    # patient_name as fallback so the LLM doesn't confuse the two people.
    _is_third_party = state.get("is_patient") is False
    _contact_full = state.get("user_name") or (
        "responsável" if _is_third_party else (state.get("social_name") or state.get("patient_name") or "paciente")
    )
    contact_first_name = _dn(_contact_full)
    contact_name = _contact_full
    is_minor = patient_age < 18
    duration_rule = _duration_rule_for(state, patient_age, first_name)

    # ── Auto-extract birth_date from recent messages if still missing ─────────
    # Handles the case where the LLM asked for birth_date but the node never
    # saved the patient's answer to state (patient_agent_node is stateless by default).
    birth_date = state.get("birth_date")
    if not birth_date:
        from app.graph.schemas import _parse_birth_date as _pbd
        msgs = list(state["messages"])
        for i in range(len(msgs) - 1, 0, -1):
            m = msgs[i]
            if m.type != "human" or not isinstance(m.content, str):
                continue
            parsed_bd = _pbd(m.content.strip())
            if not parsed_bd:
                break  # last human message is not a date — stop looking
            # Find the preceding AI message and check it asked about birth date
            prev_ai = next(
                (msgs[j] for j in range(i - 1, max(i - 5, -1), -1) if msgs[j].type == "ai"),
                None,
            )
            if prev_ai and "nascimento" in (prev_ai.content or "").lower():
                bd = datetime.strptime(parsed_bd, "%d/%m/%Y")
                today_dt = datetime.now()
                _age = today_dt.year - bd.year - ((today_dt.month, today_dt.day) < (bd.month, bd.day))
                await upsert_user(state["phone"], {"birth_date": parsed_bd, "age": _age}, user_id=state.get("user_db_id"))
                birth_date = parsed_bd
                patient_age = _age
            break

    from app.google_calendar import format_doctor_schedules
    template = EXISTING_PATIENT_SYSTEM if state.get("is_returning_patient") else NEW_PATIENT_SYSTEM
    from app.dates import build_date_reference, date_suffix_pt
    _now_recife = datetime.now(ZoneInfo("America/Recife"))
    today = build_date_reference(_now_recife)
    system_prompt = template.format(
        patient_name=first_name,
        contact_name=contact_name,
        patient_age=patient_age_display,
        birth_date=birth_date or "não informada",
        doctor=doctor_label,
        duration_rule=duration_rule,
        today=today,
        doctor_schedules=format_doctor_schedules(),
        patient_email=state.get("patient_email") or "não informado",
        financial_name=state.get("financial_name") or "não informado",
        financial_cpf=state.get("financial_cpf") or "não informado",
        financial_email=state.get("financial_email") or "não informado",
        email_rule=EMAIL_RULE,
        doctor_correction_rule=DOCTOR_CORRECTION_RULE,
        booking_fee_rule=get_booking_fee_rule(),
        cancellation_rules=CANCELLATION_RULES,
        modality_change_rule=MODALITY_CHANGE_RULE,
        pricing_rules=get_pricing_rules(datetime.now()),
        clinic_address=CLINIC_ADDRESS,
        doctors_info=get_doctors_info(),
        medical_limits_rule=MEDICAL_LIMITS_RULE,
        social_name_rule=SOCIAL_NAME_RULE,
        age_exception_rule=AGE_EXCEPTION_RULE if state.get("age_exception") else "",
        attendant_instruction_rule=ATTENDANT_INSTRUCTION_RULE,
        modality_restriction=state.get("modality_restriction") or "",
        pix_key=CORRECT_PIX_KEY,
    )

    # Inject contact-vs-patient rule when the WhatsApp contact is NOT the patient.
    # Without this, the LLM defaults to using patient_name (prominent in the prompt header)
    # to address the contact, even when they are different people.
    if state.get("is_patient") is False and state.get("user_name"):
        from app.utils import display_name as _dn
        contact_first = _dn(contact_name)
        system_prompt += (
            f"\n\nIMPORTANTE — CONTATO ≠ PACIENTE: Quem está no WhatsApp é *{contact_name}* "
            f"(o contato/responsável), NÃO o paciente *{first_name}*. "
            f"Em TODAS as mensagens, dirija-se a {contact_first} pelo nome. "
            f"Use o nome {first_name} apenas quando falar SOBRE o paciente na terceira pessoa "
            f"(ex: 'a consulta do {first_name}', 'o horário do {first_name}')."
        )

    # Inject guardian context for minor patients
    if is_minor:
        system_prompt += GUARDIAN_RULE.format(
            patient_name=first_name,
            guardian_name=state.get("guardian_name") or "não informado",
            guardian_relationship=state.get("guardian_relationship") or "responsável",
            guardian_cpf=state.get("guardian_cpf") or "não informado",
        )

    # Attendant-mode: add patient_name_override reminder (routing rules are in the base prompt)
    if state.get("silent_mode"):
        system_prompt += (
            "\n\nLEMBRETE (modo atendente): Se a instrução mencionar um nome de paciente diferente "
            "do que está no contexto da conversa, passe-o em patient_name_override ao chamar "
            "confirm_appointment para garantir que o agendamento fique no nome correto."
        )
        _pending = state.get("pending_appointment")
        if _pending:
            import json as _json
            system_prompt += (
                f"\n\nLEMBRETE (modo atendente): Há um agendamento pendente aguardando confirmação: "
                f"{_json.dumps(_pending, ensure_ascii=False)}. "
                "Se a instrução da atendente for confirmar o agendamento (ex: 'confirme', 'a paciente confirmou', "
                "'seguir fluxo'), você DEVE chamar confirm_appointment IMEDIATAMENTE com os dados acima — "
                "não chame get_available_slots nem peça mais informações."
            )

    # One-time price adjustment notice injected into the system prompt
    needs_price_notice = False
    now_dt = datetime.now(ZoneInfo("America/Recife"))
    user = await get_user_by_phone(state["phone"])
    _custom_price = (user or {}).get("custom_price")
    _fee_waived = bool((user or {}).get("booking_fee_waived", False))
    _is_exception_patient = _custom_price is not None or _fee_waived
    if user and not user.get("price_adjustment_notified_at") and not _is_exception_patient:
        needs_price_notice = True
        if (now_dt.year, now_dt.month) < (2026, 6):
            system_prompt += (
                "\n\nAVISO ÚNICO OBRIGATÓRIO NESTA MENSAGEM: Inclua no início da sua resposta, "
                "de forma natural e acolhedora, que o valor da consulta deste paciente será "
                "reajustado a partir de junho de 2026. Consultas até maio ainda têm o valor atual. "
                "Use a tabela abaixo para informar APENAS os valores correspondentes ao médico "
                "e perfil deste paciente:\n"
                "  • Dra. Bruna → até maio: R$ 600,00 / a partir de junho: R$ 700,00\n"
                "  • Dr. Júlio, adulto → até maio: R$ 600,00 / a partir de junho: R$ 700,00\n"
                "  • Dr. Júlio, 1ª consulta infantil (< 18 anos) → até maio: R$ 750,00 / a partir de junho: R$ 850,00\n"
                "  • Dr. Júlio, retorno infantil → até maio: R$ 650,00 / a partir de junho: R$ 750,00\n"
                "Se for Dr. Júlio e ainda não souber se é primeira consulta ou acompanhamento,"
                "pergunte antes de informar o valor. "
                "Faça isso independentemente do assunto da conversa."
            )
        else:
            system_prompt += (
                "\n\nAVISO ÚNICO OBRIGATÓRIO NESTA MENSAGEM: Inclua no início da sua resposta, "
                "de forma natural e acolhedora, que os valores das consultas foram atualizados "
                "em junho de 2026. Não mencione os valores anteriores. "
                "Faça isso independentemente do assunto da conversa."
            )

    # ── Per-patient pricing exception block ──────────────────────────────────
    if _is_exception_patient:
        _doctor_key = state.get("preferred_doctor") or ""
        _p_age = state.get("patient_age") or 99
        _standard_price = _expected_consultation_amount(_doctor_key, _p_age, None, now_dt)
        system_prompt += get_pricing_exception_rule(_custom_price, _fee_waived, _standard_price)

    # Inject upcoming/recent appointments so the LLM knows what already exists
    upcoming = await get_upcoming_appointments(state["phone"])
    if upcoming:
        from zoneinfo import ZoneInfo as _ZI
        _TZ = _ZI("America/Recife")
        future_lines = []
        recent_lines = []
        past_unpaid_lines = []
        stale_reschedule_lines = []
        canceled_unpaid_lines = []
        for apt in upcoming:
            dt = datetime.fromisoformat(apt["start_time"]).astimezone(_TZ)
            fee_ok = apt.get("booking_fee_paid_at") or apt.get("booking_fee_waived")
            fee_tag = "" if fee_ok else " ⚠️ TAXA DE RESERVA PENDENTE"
            # Marca o status explicitamente — sem isso, uma consulta pending_reschedule
            # fica indistinguível de uma scheduled normal no texto, mesmo quando visível
            # (caso Heitor/Ludmilla, 5581996937559, 21/07/2026).
            reschedule_tag = " 🔄 REMARCAÇÃO PENDENTE" if apt.get("status") == "pending_reschedule" else ""
            # Modalidade explícita ao lado de cada consulta — necessária para a Eva saber,
            # na CONFIRMAÇÃO DE PRESENÇA do lembrete do dia anterior, se deve enviar o
            # endereço da clínica (presencial) ou falar do link (online).
            _modality = apt.get("modality")
            modality_tag = (
                " — Presencial" if _modality == "presencial"
                else " — Online" if _modality == "online"
                else ""
            )
            _suffix = date_suffix_pt(dt.date(), _now_recife.date())
            # Prefix with the patient name — the contact may manage several patients,
            # so the LLM must not assume the appointment belongs to the active one.
            _pname = (apt.get("patient_name") or "").strip()
            _who = f"{_pname} — " if _pname else ""
            label = f"- {_who}{dt.strftime('%d/%m/%Y às %H:%M')} ({_suffix}){modality_tag} (ID: {apt['appointment_id']}){fee_tag}{reschedule_tag}"
            if apt.get("already_occurred"):
                past_unpaid_lines.append(label)
            elif apt.get("recently_ended"):
                recent_lines.append(label)
            elif apt.get("stale_reschedule"):
                stale_reschedule_lines.append(label)
            elif apt.get("canceled_unpaid"):
                _doc_key = DOCTOR_NAMES.get(apt.get("doctor_id"), "")
                _doc_label = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_doc_key, "")
                canceled_unpaid_lines.append(f"{label} — {_doc_label}" if _doc_label else label)
            else:
                future_lines.append(label)
        if future_lines:
            system_prompt += "\n\nConsultas agendadas (por paciente):\n" + "\n".join(future_lines)
        if recent_lines:
            system_prompt += "\n\nConsulta(s) recém-realizada(s) (nas últimas 48h):\n" + "\n".join(recent_lines)
        if past_unpaid_lines:
            system_prompt += (
                "\n\nConsulta(s) já realizada(s) com saldo pendente:\n"
                + "\n".join(past_unpaid_lines)
                + "\nATENÇÃO: estas consultas JÁ OCORRERAM — NUNCA diga que o saldo será "
                "quitado \"no dia da consulta\". Diga que o saldo já pode ser quitado agora."
            )
        if stale_reschedule_lines:
            system_prompt += (
                "\n\nRemarcação pendente (vaga liberada, aguardando nova data):\n"
                + "\n".join(stale_reschedule_lines)
            )
        if canceled_unpaid_lines:
            system_prompt += (
                "\n\nConsulta cancelada recentemente por falta de pagamento da taxa de reserva "
                "(vaga já foi liberada — NÃO é uma consulta ativa):\n"
                + "\n".join(canceled_unpaid_lines)
                + "\nSe o contato perguntar o horário dessa consulta, pedir para confirmar "
                "presença, ou disser que vai comparecer, responda que essa consulta foi "
                "CANCELADA por falta de pagamento da taxa de reserva e NÃO está mais ativa. "
                "Use somente o horário e o status da lista acima — NUNCA repita horário vindo "
                "de mensagens antigas desta conversa (lembretes, confirmações anteriores): esse "
                "horário pode estar desatualizado e a única fonte de verdade é a lista acima. "
                "Se o contato disser que quer remarcar, confirmar 'sim' para retomar o "
                "agendamento, ou voltar a falar sobre marcar/agendar — mesmo que pareça um pedido "
                "novo — NUNCA diga que a consulta já está confirmada/reservada sem antes: "
                "1) chamar get_available_slots para o mesmo dia e turno do horário acima, para "
                "checar se ele ainda está livre; "
                "2) perguntar explicitamente se o contato quer o MESMO horário (se a busca "
                "confirmar que ainda está disponível) ou prefere ver outras opções. "
                "NUNCA chame confirm_appointment (nem descreva a consulta como reservada) antes "
                "do contato responder a essa pergunta."
            )
    else:
        # Sem consultas o bloco acima não dizia NADA, e o silêncio não contradiz o
        # histórico: a thread guarda para sempre as confirmações antigas ("Consulta
        # registrada! ✅ ... 15/07 às 16:00"). Quando a consulta é cancelada depois
        # (taxa de reserva não paga, cancelamento pela clínica) e o paciente volta
        # semanas depois, essa mensagem antiga vira a única afirmação concreta sobre
        # agendamento no contexto — e a Eva a repete como se valesse hoje
        # (caso 5519994108706, 28/07/2026). O negativo precisa ser dito em voz alta.
        system_prompt += (
            "\n\nConsultas: NENHUMA consulta ativa para este contato no sistema.\n"
            "ATENÇÃO: mensagens antigas DESTA MESMA conversa podem conter confirmações "
            "de agendamento ('Consulta registrada', 'está reservada para o dia X'). "
            "Elas estão desatualizadas — a consulta foi cancelada ou já aconteceu. "
            "NUNCA afirme que existe consulta marcada com base no histórico da conversa: "
            "a lista acima é a única fonte de verdade. Se o paciente perguntar sobre a "
            "consulta dele, diga que não há consulta ativa e ofereça agendar."
        )

    import logging as _log
    _logger = _log.getLogger(__name__)

    # Trim history to cap per-request token usage and avoid TPM rate limits.
    # This removes orphan tool_calls and keeps only the last MAX_HISTORY_MESSAGES,
    # ensuring the first message is always human.
    clean_messages = _trim_history(state["messages"])

    # Detect whether this is the start of a new session:
    # (a) no prior AI messages at all — patient_agent_node seeing this patient for the first time, or
    # (b) there are prior AI messages but the last assistant reply was on a different calendar day.
    _has_prior_ai = any(getattr(m, "type", None) == "ai" for m in clean_messages)
    _is_new_session = not _has_prior_ai
    if _has_prior_ai:
        _last_ai_time = await get_last_assistant_message_time(state["phone"])
        if _last_ai_time is not None:
            _tz_recife = ZoneInfo("America/Recife")
            _is_new_session = _last_ai_time.astimezone(_tz_recife).date() < _now_recife.date()

    if _is_new_session:
        _hour = _now_recife.hour
        _greeting_word = "Bom dia" if _hour < 12 else ("Boa tarde" if _hour < 18 else "Boa noite")
        _session_label = "com o responsável/contato" if _is_third_party else "com o paciente"
        system_prompt += (
            f"\n\nINÍCIO DE CONVERSA: Esta é a primeira mensagem desta sessão {_session_label}. "
            f"Você DEVE começar sua resposta com '{_greeting_word}, {contact_first_name}! 😊' e se apresentar "
            f"como Eva da Clínica Psique antes de responder à solicitação."
        )

    # When the WhatsApp contact is NOT the patient, explicitly remind the LLM so it
    # doesn't address the contact as "paciente" or use "seu" for the patient's data.
    if _is_third_party:
        system_prompt += (
            f"\n\nAVISO IMPORTANTE: O contato no WhatsApp ({contact_name}) NÃO é o paciente. "
            f"Está agendando em nome do(a) paciente {first_name}. "
            f"Dirija-se ao contato pelo nome ({contact_first_name}), NUNCA como 'paciente'. "
            f"Ao pedir data de nascimento ou e-mail, deixe claro que são dados do(a) paciente {first_name} "
            f"(ex: 'Qual a data de nascimento de {first_name}?', 'Qual o e-mail de {first_name}?')."
        )

    # Detect when the last AI message was the appointment confirmation summary
    # ("Só confirmar antes de registrar") and the patient just replied affirmatively.
    # In this situation the ONLY correct next action is confirm_appointment.
    # Without this guard, the NEW_PATIENT_SYSTEM step-1 instruction ("if the user
    # mentioned a day, call get_available_slots immediately") fires on the human's
    # confirmation reply and Eva re-queries slots instead of confirming.
    import re as _re_marker
    _CONFIRM_SUMMARY_RE = _re_marker.compile(r'Só\s+(?:para\s+)?confirmar\s+antes\s+de\s+registrar')
    # 🙏 is a thank-you gesture, NOT a booking confirmation — keep it out of this set
    _AFFIRMATIVE = {"sim", "pode", "confirma", "confirmo", "ok", "isso", "pode ser",
                    "tá", "ta", "tá bom", "ta bom", "perfeito", "ótimo", "otimo",
                    "certo", "claro", "exato", "👍", "✅", "pode confirmar",
                    "confirmar", "quero", "quero confirmar", "vai", "bora", "fechado"}
    _last_human_content = ""
    for _m in reversed(clean_messages):
        if not _last_human_content and getattr(_m, "type", "") == "human":
            _last_human_content = (getattr(_m, "content", "") or "").strip().lower()
            break
    # Search only the last 6 messages for the confirmation summary marker.
    # Searching all history causes false positives: an old "Só confirmar antes de registrar"
    # from a previous booking would trigger the guard on an unrelated "Pode confirmar"
    # reply to a reminder message.
    _summary_in_recent_ai = any(
        bool(_CONFIRM_SUMMARY_RE.search(getattr(_m, "content", "") or ""))
        for _m in clean_messages[-6:]
        if getattr(_m, "type", "") == "ai"
    )
    # Do NOT re-fire the guard if confirm_appointment was already called successfully
    # in this conversation (prevents double-booking when patient sends a non-affirmative
    # gesture like 🙏 that the LLM interprets as confirmation and books the slot).
    _already_confirmed = any(
        getattr(_m, "type", "") == "tool"
        and "registrada" in (getattr(_m, "content", "") or "").lower()
        and any(
            tc.get("name") == "confirm_appointment"
            for ai_m in clean_messages
            if getattr(ai_m, "type", "") == "ai"
            for tc in (getattr(ai_m, "tool_calls", None) or [])
        )
        for _m in clean_messages
    )
    _awaiting_confirm = (
        _summary_in_recent_ai
        and any(w in _last_human_content for w in _AFFIRMATIVE)
        and not _already_confirmed
    )
    if _awaiting_confirm:
        _confirm_instruction = (
            "⚠️ ATENÇÃO — AÇÃO ÚNICA OBRIGATÓRIA AGORA: "
            "O paciente acabou de confirmar o agendamento que você apresentou na mensagem anterior. "
            "Você JÁ tem todos os dados necessários no histórico da conversa (data, hora, médico, paciente, modalidade). "
            "Chame confirm_appointment IMEDIATAMENTE com esses dados. "
            "NÃO chame get_available_slots. NÃO faça perguntas. NÃO busque novos horários. "
            "Apenas chame confirm_appointment agora.\n\n"
        )
        system_prompt = _confirm_instruction + system_prompt

    # When we just transitioned from collect_info in the same turn, tell the agent
    # exactly what the user was trying to do so it doesn't get distracted by
    # unrelated context such as upcoming appointments.
    pending_action = state.get("pending_action")
    if pending_action == "request_document":
        system_prompt += (
            "\n\nAÇÃO IMEDIATA: O cadastro foi concluído agora mesmo nesta mesma mensagem. "
            "O usuário havia solicitado um documento (nota fiscal, laudo, receita, etc.). "
            "Chame request_document agora para processar essa solicitação. "
            "NÃO mencione consultas agendadas nem inicie outro assunto."
        )

    system_prompt += "\n\n" + NO_REPEAT_ANSWER_RULE

    messages = [SystemMessage(content=system_prompt), *clean_messages]
    response = await _get_agent_llm().ainvoke(messages)

    _logger.info("AGENT_DEBUG tool_calls=%s content_len=%s",
                 [t["name"] for t in response.tool_calls] if response.tool_calls else [],
                 len(response.content) if response.content else 0)

    # ── Guard: comprovante no turno exige register_payment ────────────────────
    # A Eva já respondeu a um comprovante com texto puro ("a taxa de reserva já
    # foi recebida") sem chamar register_payment — booking_fee_paid_at ficou NULL,
    # o cron cobrou a paciente de novo 2h depois e a consulta entrou na fila do
    # cancelamento automático (caso Isadora de Sousa Costa, 5581988417858,
    # 07/08/2026; mesmo padrão em Denise Alencar 23/07 e Juliana Feitosa 04/08).
    # O prompt já proíbe isso seis vezes; a garantia real precisa ser código: se
    # a mensagem do turno é um comprovante e a resposta não chama
    # register_payment, injeta a tool call — o ToolNode executa e a LLM volta
    # para redigir a resposta com o resultado real. Pula em silent_mode (registro
    # guiado pela atendente, que informa o valor), como os demais guards.
    from app.media import IMAGE_TAG as _IMG_TAG, is_payment_receipt_message as _is_receipt
    _last_h_idx = next(
        (i for i in range(len(clean_messages) - 1, -1, -1)
         if getattr(clean_messages[i], "type", "") == "human"),
        None,
    )
    _receipt_needs_register = False
    _receipt_content = ""
    if _last_h_idx is not None and not state.get("silent_mode"):
        _receipt_content = str(getattr(clean_messages[_last_h_idx], "content", "") or "")
        if _is_receipt(_receipt_content):
            # Volta do ToolNode no mesmo turno: register_payment já rodou para
            # esta mensagem — reinjetar aqui viraria loop de registros duplicados.
            _register_ran = any(
                (getattr(m, "type", "") == "tool" and getattr(m, "name", "") == "register_payment")
                or any(
                    tc.get("name") == "register_payment"
                    for tc in (getattr(m, "tool_calls", None) or [])
                )
                for m in clean_messages[_last_h_idx + 1:]
            )
            _receipt_needs_register = not _register_ran

    if _receipt_needs_register and not any(
        tc.get("name") == "register_payment" for tc in (response.tool_calls or [])
    ):
        import re as _re_rp
        import uuid as _uuid_rp
        _dl_m = _re_rp.search(r"\[drive_link:(\S+?)\]", _receipt_content)
        _img_idx = _receipt_content.find(_IMG_TAG)
        _img_desc = (
            _receipt_content[_img_idx + len(_IMG_TAG):].strip()
            if _img_idx != -1 else _receipt_content
        )
        _am_m = _re_rp.search(r"R\$\s*([\d.]*\d+,\d{2})", _img_desc)
        _forced_rp = {
            "name": "register_payment",
            "args": {
                "amount": _am_m.group(1) if _am_m else "?",
                "drive_link": _dl_m.group(1) if _dl_m else "",
                "image_description": _img_desc,
            },
            "id": str(_uuid_rp.uuid4()),
            "type": "tool_call",
        }
        _logger.warning(
            "GUARD_RECEIPT_REGISTER: comprovante no turno sem register_payment na resposta "
            "(tool_calls=%s) — injetando register_payment. phone=%s",
            [tc.get("name") for tc in (response.tool_calls or [])], state.get("phone"),
        )
        _kept_tcs = list(response.tool_calls or [])
        response = AIMessage(
            # Sem tool calls, o texto é uma afirmação solta sobre um pagamento
            # não registrado ("taxa recebida") — descarta em vez de arquivar a
            # alucinação no histórico. Com tool calls legítimas, preserva.
            content=response.content if _kept_tcs else "",
            tool_calls=_kept_tcs + [_forced_rp],
        )

    # ── Guard: promessa de documento no turno exige request_document ───────────
    # A Eva entende o pedido, diz "vou solicitar a emissão da nota fiscal... tudo
    # bem?" e NÃO chama request_document — o pedido não é registrado em lugar
    # nenhum (nem documents, nem evento, nem planilha) e a clínica fica sem sinal
    # de que o documento é necessário (caso Carlos Henrique de Almeida,
    # 5581995163026, 24/08/2026; mesma classe do caso atestado Bento/Sandro). O
    # prompt já proíbe o "vou solicitar, tudo bem?" (linha 1153); a garantia real
    # é código, espelhando o GUARD_RECEIPT_REGISTER acima: se a resposta promete
    # um documento e não chama a ferramenta, injeta request_document com o tipo
    # inferido da narração e o e-mail já registrado. O ToolNode executa e a LLM
    # volta para redigir a resposta com o resultado real. Pula em silent_mode
    # (registro guiado pela atendente), como os demais guards.
    _doc_acted = any(
        tc.get("name") in ("request_document", "nudge_doctor_document")
        for tc in (response.tool_calls or [])
    )
    if _last_h_idx is not None and not state.get("silent_mode") and not _doc_acted:
        # Já rodou request_document/nudge neste turno (volta do ToolNode)? Não
        # reinjeta — a resposta final pode conter "será enviada" no passado.
        _doc_ran = any(
            (getattr(m, "type", "") == "tool"
             and getattr(m, "name", "") in ("request_document", "nudge_doctor_document"))
            or any(
                tc.get("name") in ("request_document", "nudge_doctor_document")
                for tc in (getattr(m, "tool_calls", None) or [])
            )
            for m in clean_messages[_last_h_idx + 1:]
        )
        _promised_type = None if _doc_ran else _detect_document_promise(str(response.content or ""))
        # Caminho de HANDOFF engole o pedido de emissão: quando a Eva roteia para
        # transfer_to_human (verbo "buscar", urgência, ou fora do horário — que é o
        # próprio retorno da tool) num turno em que o paciente pediu EMISSÃO de
        # documento, sua resposta é sobre a transferência, não uma promessa de
        # documento, então _detect_document_promise (que olha o texto da Eva) não
        # dispara. Aqui o sinal vem da MENSAGEM DO PACIENTE. Casos Davi/Daniel
        # (verbo "buscar") e Eduardo Lyra (urgência + fora do horário). Ver
        # receita-providenciar-vs-buscar.md.
        _handoff_this_turn = any(
            tc.get("name") == "transfer_to_human" for tc in (response.tool_calls or [])
        )
        _patient_msg = str(getattr(clean_messages[_last_h_idx], "content", "") or "")
        if not _promised_type and not _doc_ran and _handoff_this_turn:
            _promised_type = _detect_document_emission_request(_patient_msg)
        _doc_email = (state.get("patient_email") or "").strip()
        # Sem e-mail, injetar gravaria um pedido que não chega ao paciente —
        # pedir o e-mail antes é responsabilidade do prompt. Não injeta.
        if _promised_type and _doc_email:
            import uuid as _uuid_doc
            _forced_doc_args = {"document_type": _promised_type, "patient_email": _doc_email}
            # receita sem medication_note faz a tool só re-perguntar "qual
            # medicação?" (não registra). No caminho de handoff o bot fica inativo
            # e não veria a resposta — então passa a própria mensagem do paciente
            # como nota da medicação, garantindo o registro (e a detecção de
            # medicação controlada, que faz substring na nota).
            if _promised_type == "receita" and _handoff_this_turn:
                _forced_doc_args["medication_note"] = _patient_msg.strip()
            _forced_doc = {
                "name": "request_document",
                "args": _forced_doc_args,
                "id": str(_uuid_doc.uuid4()),
                "type": "tool_call",
            }
            _logger.warning(
                "GUARD_DOC_PROMISE: promessa de %s na resposta sem request_document "
                "(tool_calls=%s) — injetando request_document. phone=%s",
                _promised_type,
                [tc.get("name") for tc in (response.tool_calls or [])], state.get("phone"),
            )
            _kept_doc_tcs = list(response.tool_calls or [])
            response = AIMessage(
                # Sem tool calls, o texto é a promessa solta ("vou solicitar...
                # tudo bem?") sobre um documento não registrado — descarta para a
                # LLM redigir a resposta a partir do resultado real da tool.
                content=response.content if _kept_doc_tcs else "",
                tool_calls=_kept_doc_tcs + [_forced_doc],
            )

    # ── Guard: block premature confirm_appointment ────────────────────────────
    # If the LLM is trying to call confirm_appointment but the patient has NOT
    # yet confirmed (pending_appointment is not set), intercept the call:
    # extract the slot data from the tool args, send the confirmation summary
    # to the patient, and store pending_appointment — bypassing the tool call.
    # Exception: attendant-mode (silent_mode) skips this guard.
    if (
        response.tool_calls
        and not state.get("silent_mode")
        and not state.get("pending_appointment")
        # Comprovante no turno CONTA como confirmação (prompt: "uma imagem de
        # comprovante enviada como resposta a este resumo TAMBÉM vale como
        # confirmação afirmativa") — interceptar aqui descartaria o
        # register_payment injetado pelo GUARD_RECEIPT_REGISTER acima.
        and not _receipt_needs_register
        and any(tc.get("name") == "confirm_appointment" for tc in response.tool_calls)
    ):
        _confirm_tc = next(tc for tc in response.tool_calls if tc.get("name") == "confirm_appointment")
        _args = _confirm_tc.get("args") or {}
        _slot_dt = _args.get("slot_datetime", "")
        _duration = _args.get("slot_duration_minutes", 60)
        _modality = _args.get("modality", "")

        # Build the human-readable summary from the slot datetime
        _doctor_key = state.get("preferred_doctor") or ""
        _doctor_lbl = {"julio": "Dr. Júlio", "bruna": "Dra. Bruna"}.get(_doctor_key, "médico(a)")
        _patient_nm = state.get("patient_name") or state.get("user_name") or "Paciente"
        _mod_lbl = {"presencial": "Presencial", "online": "Online"}.get(_modality, _modality.capitalize() if _modality else "—")

        _summary = ""
        try:
            from datetime import datetime as _dt2
            from zoneinfo import ZoneInfo as _ZI2
            _slot = _dt2.fromisoformat(_slot_dt).astimezone(_ZI2("America/Recife"))
            _weekdays_pt = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
            _weekday_name = _weekdays_pt[_slot.weekday()]
            _summary = (
                f"Só confirmar antes de registrar: 😊\n"
                f"📅 {_weekday_name}, dia {_slot.strftime('%d/%m')}, às {_slot.strftime('%H:%M')}\n"
                f"👨‍⚕️ {_doctor_lbl}\n"
                f"👤 Paciente: {_patient_nm}\n"
                f"📍 Modalidade: {_mod_lbl}\n"
                f"Posso confirmar o agendamento?"
            )
        except Exception:
            _logger.exception("GUARD_PREMATURE_CONFIRM: failed to build summary for slot=%s", _slot_dt)

        if _summary:
            _logger.info("GUARD_PREMATURE_CONFIRM: intercepted confirm_appointment, sending summary instead. slot=%s", _slot_dt)
            await send_text(state["phone"], _summary)
            await save_message(state["phone"], "assistant", _summary)
            from langchain_core.messages import AIMessage as _AIMsg2
            _pending_from_args = {
                "slot_datetime": _slot_dt,
                "slot_duration_minutes": _duration,
                "modality": _modality,
                "doctor": _doctor_key,
            }
            return {
                "messages": [_AIMsg2(content=_summary)],
                "pending_appointment": _pending_from_args,
            }

    # ── Guard: block hallucinated clinic address ────────────────────────────
    # A Eva já alucinou endereço com o endereço correto presente no system prompt
    # (Rio de Janeiro, 08/07/2026). sanitize_clinic_address é uma whitelist: troca
    # qualquer endereço que não seja o canônico. Roda antes do GUARD_TRANSFER
    # abaixo, para que ele veja o conteúdo já corrigido.
    if not response.tool_calls and response.content:
        _safe_content, _addr_fixed = sanitize_clinic_address(response.content)
        if _addr_fixed:
            _logger.warning(
                "GUARD_WRONG_ADDRESS: blocked wrong address phone=%s original=%s",
                state["phone"], response.content,
            )
            response = AIMessage(content=_safe_content)

    # ── Guard: catch promised-but-not-called transfer_to_human ──────────────
    # If the LLM said "vou transferir" in its text but produced no tool calls,
    # force-call transfer_to_human so the handoff actually happens.
    # Skip in silent_mode (attendant instructions) — transfer is blocked there.
    if (
        not response.tool_calls
        and not state.get("silent_mode")
        and response.content
    ):
        _transfer_phrases = (
            "vou transferir",
            "vou te transferir",
            "transferindo para",
            "vou encaminhar",
        )
        _resp_lower = response.content.lower()
        _promised_transfer = any(p in _resp_lower for p in _transfer_phrases)
        if _promised_transfer:
            _logger.info("GUARD_TRANSFER: LLM promised transfer but called no tool — forcing transfer_to_human")
            # Send Eva's message to the patient first, then execute the transfer tool
            await send_text(state["phone"], response.content)
            await save_message(state["phone"], "assistant", response.content)
            from langchain_core.messages import AIMessage as _AIMsg3, ToolMessage as _TMsg3
            import uuid as _uuid3
            _tc_id = str(_uuid3.uuid4())
            _forced_tc = {"name": "transfer_to_human", "args": {"reason": "Eva prometeu transferir mas não chamou a tool — transferência forçada pelo guard."}, "id": _tc_id, "type": "tool_use"}
            _forced_ai = _AIMsg3(content=response.content, tool_calls=[_forced_tc])
            from app.graph.tools import transfer_to_human as _transfer_fn
            _transfer_result = await _transfer_fn.coroutine(
                reason="Paciente aguarda atendimento. Eva prometeu transferir.",
                state=state,
                config=config,
            )
            _tool_msg = _TMsg3(content=str(_transfer_result), tool_call_id=_tc_id)
            return {"messages": [_forced_ai, _tool_msg]}

    # ── Guarda: horário errado no resumo de confirmação (modelo barato) ──────
    # O gpt-5.6-luna às vezes escreve, no resumo "Só confirmar antes de registrar",
    # um horário que o paciente não escolheu (tipicamente 08:00, o 1º da grade).
    # Corrige o texto ANTES de enviar e ANTES de virar pending_appointment — assim
    # o que o paciente lê bate com o que será agendado. Só age com evidência
    # confiável (interseção única entre o pedido do paciente e a oferta real).
    if not response.tool_calls and response.content:
        try:
            _corrected_summary = _correct_confirmation_summary_time(response.content, state)
        except Exception:
            _corrected_summary = None
            _pa_logger.exception("CONFIRM_TIME_GUARD failed phone=%s", state.get("phone"))
        if _corrected_summary and _corrected_summary != response.content:
            _pa_logger.info(
                "CONFIRM_TIME_GUARD corrected confirmation summary time phone=%s",
                state.get("phone"),
            )
            response.content = _corrected_summary

    # Only send to WhatsApp when the LLM produces a final text (no tool calls)
    if not response.tool_calls and response.content:
        phone = state["phone"]

        # Detect internal-note responses by content prefix ONLY.
        # Do NOT use silent_mode here: when an attendant sends a command, Eva must
        # still deliver patient-facing messages to the patient via WhatsApp.
        # silent_mode only controls the system-prompt instruction (see below).
        _INTERNAL_PREFIXES = (
            "nota para a equipe",
            "nota interna",
            "nota para equipe",
            "[nota interna]",
            "[nota para a equipe]",
        )
        _content_lower = response.content.lstrip().lower()
        is_internal = any(_content_lower.startswith(p) for p in _INTERNAL_PREFIXES)

        if is_internal:
            # Post as Chatwoot private note.
            # Fallback to WhatsApp if conv_id is missing or the API call fails —
            # better the message arrive in the wrong place than disappear silently.
            import logging as _log
            _node_logger = _log.getLogger(__name__)
            conv_id = get_conversation_id(phone)
            _node_logger.info("PRIVATE_NOTE attempt phone=%s conv_id=%s", phone, conv_id)
            posted = False
            if conv_id:
                try:
                    await add_private_note(conv_id, response.content)
                    posted = True
                except Exception:
                    _node_logger.exception(
                        "PRIVATE_NOTE FAILED phone=%s conv_id=%s — falling back to WhatsApp", phone, conv_id
                    )
            if not posted:
                _node_logger.warning(
                    "PRIVATE_NOTE no conv_id or post failed — sending to WhatsApp phone=%s", phone
                )
                await send_text(phone, response.content)
            await save_message(phone, "assistant", response.content)
        else:
            await send_text(phone, response.content)
            await save_message(phone, "assistant", response.content)
            if needs_price_notice:
                await upsert_user(phone, {"price_adjustment_notified_at": now_dt.isoformat()}, user_id=state.get("user_db_id"))

    # Keep silent_mode alive while a tool call is pending — the ToolNode reads state
    # right after this update is applied, and force_encaixe (and other attendant-only
    # tool behavior) depends on silent_mode still being True at that point. Only clear
    # it once the LLM produces its final text response (no more tool calls this turn).
    update: dict = {"messages": [response], **_sync_updates}
    if not response.tool_calls:
        update["silent_mode"] = False
    if pending_action:
        update["pending_action"] = None

    # If the LLM just sent the confirmation summary, extract and store the slot data
    # so the next turn can confirm directly without going through the LLM again.
    if not response.tool_calls and response.content:
        _parsed = _extract_pending_appointment(response.content, state)
        if _parsed:
            update["pending_appointment"] = _parsed
            _logger.info("PENDING_APPT_STORED slot=%s", _parsed.get("slot_datetime"))
        elif state.get("pending_appointment"):
            # LLM replied with something other than a new summary — clear stale pending
            update["pending_appointment"] = None

    # If preferred_doctor is missing from state, check whether update_preferred_doctor
    # was just called (state may not reflect it yet because ToolNode only updates messages).
    if not state.get("preferred_doctor"):
        all_msgs = list(state["messages"]) + [response]
        for msg in reversed(all_msgs):
            tool_calls = getattr(msg, "tool_calls", None) or []
            for tc in tool_calls:
                if tc.get("name") == "update_preferred_doctor":
                    doctor_val = (tc.get("args") or {}).get("doctor")
                    if doctor_val:
                        update["preferred_doctor"] = doctor_val
                        if state.get("user_db_id"):
                            from app.database import DOCTOR_IDS as _DIDS
                            try:
                                await upsert_user(state["phone"], {"doctor_id": _DIDS.get(doctor_val)}, user_id=state.get("user_db_id"))
                            except Exception:
                                _logger.exception("Failed to persist preferred_doctor after update_preferred_doctor tool call")
                        break
            if "preferred_doctor" in update:
                break

    return update
