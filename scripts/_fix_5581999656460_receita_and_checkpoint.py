"""
Caso Kimmy Cavalcanti Mastana (5581999656460) — 20/07/2026.

Root cause: em 20/07 15:35, a IA entrou em loop degenerativo gerando um tool_call
(confirm_attendance) e estourou o limite de completion_tokens (32768) antes de
fechar o JSON dos args. O LangChain classificou isso como invalid_tool_call (não
tool_call), então a recuperação de "orphaned tool_calls" em main.py (que só olha
AIMessage.tool_calls) nunca disparou. Toda mensagem seguinte da paciente (pedido
de receita, Zoloft 100mg 2 caixas, "pra enviar por whatsapp") e a nota da
atendente bateram no mesmo erro 400 da OpenAI, em silêncio total.

Este script:
  1. Registra a solicitação de receita (planilha + e-mail ao Dr. Júlio), igual ao
     tool request_document faria.
  2. Repara o checkpoint injetando uma ToolMessage de erro para o invalid_tool_call
     órfão, destravando a conversa.
  3. Envia uma mensagem de confirmação para a paciente via WhatsApp.
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581999656460@s.whatsapp.net"
PATIENT_NAME = "Kimmy Cavalcanti Mastana"
PATIENT_AGE = 19
PATIENT_EMAIL = "darleideregiamastana@yahoo.com.br"
DOCTOR_KEY = "julio"
DOCTOR_LABEL = "Dr. Júlio"
MEDICATION_NOTE = "Zoloft 100 mg, 2 caixas"


async def register_receita():
    from app.database import get_supabase, log_event, DOCTOR_IDS
    from app.google_sheets import append_document_request
    from app.email_sender import send_document_request_email

    client = await get_supabase()
    doctor_id = DOCTOR_IDS[DOCTOR_KEY]

    result = await client.from_("doctors").select("agenda_id").eq("doctor_id", doctor_id).single().execute()
    doctor_email = result.data.get("agenda_id", "") if result.data else ""

    await client.from_("documents").insert({
        "content": "Solicitação de receita",
        "metadata": {
            "type": "receita",
            "patient_name": PATIENT_NAME,
            "patient_email": PATIENT_EMAIL,
            "doctor_id": doctor_id,
            "phone": PHONE,
            "financial_name": None,
            "financial_cpf": None,
            "financial_email": None,
        },
    }).execute()

    await log_event("document_requested", PHONE, {
        "document_type": "receita",
        "patient_name": PATIENT_NAME,
    })

    await append_document_request(
        PATIENT_NAME, PATIENT_AGE, PHONE, PATIENT_EMAIL, "receita", MEDICATION_NOTE,
        doctor_name=DOCTOR_LABEL, patient_cpf="", financial_name="", financial_cpf="", financial_email="",
    )
    print("OK: planilha de documentos atualizada.")

    await send_document_request_email(
        DOCTOR_KEY, doctor_email, PATIENT_NAME, PATIENT_AGE, PHONE, PATIENT_EMAIL, "receita",
        financial_name="", financial_cpf="", financial_email="",
    )
    print(f"OK: e-mail enviado para {DOCTOR_LABEL} ({doctor_email}).")


async def repair_checkpoint():
    from app.graph.graph import build_graph
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import ToolMessage
    import psycopg, os

    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": PHONE}}

        snapshot = await graph.aget_state(config)
        msgs = snapshot.values.get("messages") or []
        last_ai = next((m for m in reversed(msgs) if getattr(m, "type", None) == "ai"), None)
        invalid_calls = getattr(last_ai, "invalid_tool_calls", []) if last_ai else []
        answered_ids = {getattr(m, "tool_call_id", None) for m in msgs if getattr(m, "type", None) == "tool"}
        orphaned = [c for c in invalid_calls if c["id"] not in answered_ids]

        if not orphaned:
            print("Nada para reparar (nenhum invalid_tool_call órfão encontrado).")
            return

        error_msgs = [
            ToolMessage(content="Erro interno — tente novamente.", tool_call_id=c["id"])
            for c in orphaned
        ]
        await graph.aupdate_state(config, {"messages": error_msgs}, as_node="patient_agent")
        print(f"OK: checkpoint reparado — {len(orphaned)} invalid_tool_call(s) respondido(s): "
              f"{[c['id'] for c in orphaned]}")


async def notify_patient():
    from app.whatsapp import send_text
    msg = (
        "Oi, Kimmy! Peço desculpas pela demora — tivemos uma falha técnica aqui e sua "
        "mensagem não foi respondida a tempo. 🙏\n\n"
        "Já registrei sua solicitação de receita de Zoloft 100mg (2 caixas) e encaminhei "
        "para o Dr. Júlio. Assim que ele emitir, te enviamos por aqui mesmo. Se precisar "
        "de mais alguma coisa, estou à disposição!"
    )
    await send_text(PHONE, msg)
    print("OK: mensagem de confirmação enviada à paciente.")


async def main():
    await register_receita()
    await repair_checkpoint()
    await notify_patient()


asyncio.run(main())
