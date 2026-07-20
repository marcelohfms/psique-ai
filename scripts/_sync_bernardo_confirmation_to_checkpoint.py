"""
One-off: sincroniza no checkpoint da Eva (LangGraph state) a mensagem de
confirmação da consulta dividida do Bernardo Rabelo Porto Ferreira, que foi
enviada manualmente ao contato (Mônica) fora do fluxo normal do Chatwoot —
por isso não passou pelo auto-sync de mensagem pública de atendente
(main.py:1091-1102, ver memory feedback_chatwoot_public_msg_autosync).

Sem isso, o checkpoint fica sem registro da mensagem e a Eva não tem
contexto de que a correção já foi comunicada ao contato.

Replica exatamente a mesma operação do auto-sync: AIMessage no node
"patient_agent".
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

PHONE = "5581991320003@s.whatsapp.net"

CONFIRMATION_TEXT = """Oi, Mônica! 😊 Tudo certo com o agendamento do Bernardo — ajustamos aqui e ficou assim:

📅 1ª hora — responsáveis
Quinta, 23/07, às 19h
👨‍⚕️ Dr. Júlio
📍 Presencial — RioMar Trade Center, Torre 1, Andar 25, Sala 2502, Av. República do Líbano, 251 — Recife-PE

📅 2ª hora — com o Bernardo
Quinta, 30/07, às 15h
👨‍⚕️ Dr. Júlio
📍 Presencial — mesmo endereço

A taxa de reserva já está confirmada, não precisa se preocupar com isso novamente. ✅

Qualquer dúvida, estou à disposição! 🩷"""


async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import AIMessage
    from app.graph.graph import build_graph

    conn_string = os.getenv("SUPABASE_CONNECTION_STRING")
    if not conn_string:
        raise RuntimeError("SUPABASE_CONNECTION_STRING não configurado no .env")

    async with await AsyncConnection.connect(
        conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
    ) as conn:
        checkpointer = AsyncPostgresSaver(conn)
        await checkpointer.setup()
        chatbot = build_graph(checkpointer=checkpointer)

        config = {"configurable": {"thread_id": PHONE, "phone": PHONE}}

        await chatbot.aupdate_state(
            config,
            {"messages": [AIMessage(content=CONFIRMATION_TEXT)]},
            as_node="patient_agent",
        )
        print("Mensagem sincronizada no checkpoint como AIMessage (node=patient_agent).")

        state = await chatbot.aget_state(config)
        msgs = state.values.get("messages", [])
        print(f"\nTotal de mensagens no checkpoint agora: {len(msgs)}")
        if msgs:
            last = msgs[-1]
            print(f"Última mensagem ({type(last).__name__}): {last.content[:120]}...")


asyncio.run(main())
