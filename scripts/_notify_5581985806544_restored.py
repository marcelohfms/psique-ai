import asyncio
from dotenv import load_dotenv
load_dotenv()

MESSAGE = (
    "Olá, Aiza! 😊 Encontramos aqui que sua consulta com a *Dra. Bruna* no dia "
    "*15/07 às 16:00* foi cancelada por engano do nosso sistema — a taxa de reserva "
    "já havia sido isentada, como combinado, mas o cancelamento automático não "
    "reconheceu isso.\n\n"
    "Já corrigimos: a consulta do Eduardo está confirmada novamente para "
    "*15/07 às 16:00* com a Dra. Bruna, e a taxa de reserva continua isenta. "
    "Não é necessário nenhum pagamento. Pedimos desculpas pelo transtorno! 🙏"
)


async def main():
    from app.whatsapp import send_text

    phone = "5581985806544@s.whatsapp.net"
    await send_text(phone, MESSAGE)
    print("WhatsApp enviado.")

    import os
    conn_string = os.environ.get("SUPABASE_CONNECTION_STRING")
    if conn_string:
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from app.graph.graph import build_graph
        from langchain_core.messages import AIMessage

        pg_conn = await AsyncConnection.connect(
            conn_string, autocommit=True, prepare_threshold=None, row_factory=dict_row,
        )
        try:
            checkpointer = AsyncPostgresSaver(pg_conn)
            graph = build_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": phone, "phone": phone}}
            await graph.aupdate_state(config, {"messages": [AIMessage(content=MESSAGE)]}, as_node="patient_agent")
            print("Checkpoint atualizado.")
        finally:
            await pg_conn.close()
    else:
        print("SUPABASE_CONNECTION_STRING não definido — checkpoint não atualizado.")

    from app.database import save_message
    await save_message(phone, "assistant", MESSAGE)
    print("Mensagem salva em messages.")

asyncio.run(main())
