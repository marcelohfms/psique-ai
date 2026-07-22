"""
One-off: corrige o checkpoint e o registro da Natalia Camara Lima Alves De Souza
Pimentel (5581996332827) após bug de remarcação <24h.

1. Remove as 2 mensagens duplicadas (a atendente enviou a cobrança da nova taxa
   manualmente e ela foi sincronizada 3x no checkpoint).
2. Zera booking_fee_paid_at / reseta created_at do agendamento de 06/07 07:30,
   já que a taxa antiga (paga em 26/06) não pode ser aproveitada — a mensagem de
   cobrança da nova taxa já foi enviada manualmente pela atendente.
"""
import asyncio, os
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
load_dotenv()

TZ = ZoneInfo("America/Recife")
PHONE = "5581996332827"
APPOINTMENT_ID = "bb3psfo966q5vqhq0kc4bifpms"
DUPLICATE_MSG_IDS = [
    "e63a5da0-64d1-450c-b3db-f1f70c59fb6c",
    "2dbc1392-a9cf-4849-8171-9807faf45c37",
]


async def main():
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langchain_core.messages import RemoveMessage
    from app.graph.graph import build_graph
    from app.database import get_supabase, log_event

    # 1. Dedupe checkpoint messages
    conn_str = os.environ["SUPABASE_CONNECTION_STRING"]
    pg_conn = await AsyncConnection.connect(
        conn_str, autocommit=True, prepare_threshold=None, row_factory=dict_row
    )
    checkpointer = AsyncPostgresSaver(pg_conn)
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": f"{PHONE}@s.whatsapp.net"}}

    await graph.aupdate_state(
        config,
        {"messages": [RemoveMessage(id=mid) for mid in DUPLICATE_MSG_IDS]},
    )
    print("✅ Mensagens duplicadas removidas do checkpoint.")
    await pg_conn.close()

    # 2. Corrige o agendamento: taxa antiga não pode ser aproveitada
    client = await get_supabase()
    now = datetime.now(TZ)
    await client.from_("appointments").update({
        "created_at": now.isoformat(),
        "booking_fee_paid_at": None,
        "booking_fee_waived": False,
        "updated_at": now.isoformat(),
    }).eq("appointment_id", APPOINTMENT_ID).execute()
    print("✅ Agendamento atualizado: created_at resetado e booking_fee_paid_at zerado.")

    await log_event("booking_fee_charge_requested", f"{PHONE}@s.whatsapp.net", {
        "appointment_id": APPOINTMENT_ID,
        "reason": "correção manual — taxa antiga indevidamente aproveitada após remarcação <24h; cobrança enviada manualmente pela atendente",
        "amount": "100,00",
    })
    print("✅ Evento registrado.")


asyncio.run(main())
