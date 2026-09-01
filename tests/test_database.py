"""Testes da camada app.database — foco em save_message.

Persistir a mensagem do paciente aqui é a ÚNICA gravação em `messages`: o grafo
só grava as respostas da Eva (app/graph/nodes.py), e o webhook chama save_message
uma vez, logo antes de despachar para o grafo (app/main.py:772 e :1472). Se este
insert falha em silêncio, o comprovante que a paciente acabou de enviar some da
tabela, mesmo com o evento payment_receipt_registered e o arquivo no Drive já
gravados — e as guardas que leem `messages` (find_receipt_in_conversation no cron
de cobrança) ficam cegas. Caso Fernanda 5587996373892 (01/09/2026).
"""
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.database import save_message

RECEIPT = "[imagem]: COMPROVANTE DE PAGAMENTO: R$ 100,00 [drive_link:https://drive/x]"
PHONE = "5587996373892@s.whatsapp.net"


@pytest.mark.asyncio
async def test_save_message_persiste_no_primeiro_sucesso(mock_supabase):
    """Caminho feliz: um insert, nenhum retry, nenhuma espera."""
    _client, table, execute = mock_supabase
    with patch("app.database.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await save_message(PHONE, "user", RECEIPT)

    assert execute.await_count == 1
    sleep.assert_not_awaited()
    row = table.insert.call_args.args[0]
    assert row["role"] == "user"
    assert "COMPROVANTE DE PAGAMENTO" in row["content"]


@pytest.mark.asyncio
async def test_save_message_reintenta_e_persiste_apos_falha_transitoria(mock_supabase):
    """Uma falha transitória no insert não pode derrubar o comprovante: reintenta
    e grava. Antes, com `except Exception: pass`, a 1ª falha descartava a linha."""
    _client, _table, execute = mock_supabase
    execute.side_effect = [RuntimeError("connection reset"), MagicMock(data=[])]

    with patch("app.database.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await save_message(PHONE, "user", RECEIPT)

    assert execute.await_count == 2  # reintentou e conseguiu gravar
    sleep.assert_awaited()           # esperou entre as tentativas


@pytest.mark.asyncio
async def test_save_message_loga_e_nao_propaga_quando_todas_falham(mock_supabase, caplog):
    """Falha persistente: NÃO propaga (contrato fire-and-forget) mas também NÃO
    fica em silêncio — loga para que o buraco seja auditável depois."""
    _client, _table, execute = mock_supabase
    execute.side_effect = RuntimeError("supabase down")

    with patch("app.database.asyncio.sleep", new_callable=AsyncMock), \
         caplog.at_level(logging.ERROR):
        await save_message(PHONE, "user", RECEIPT)  # não deve levantar

    assert execute.await_count >= 2  # tentou mais de uma vez antes de desistir
    assert any("SAVE_MESSAGE_FAILED" in r.getMessage() for r in caplog.records), \
        "a falha final tem que ser logada, não engolida em silêncio"
