import pytest
from unittest.mock import AsyncMock, patch

import app.supabase_client as sc


@pytest.mark.asyncio
async def test_get_supabase_cria_cliente_uma_vez_so():
    """O cliente é singleton: a segunda chamada reusa, não recria."""
    sentinel = object()
    with patch.object(sc, "_supabase", None), \
         patch.object(sc, "acreate_client", new_callable=AsyncMock,
                      return_value=sentinel) as create:
        first = await sc.get_supabase()
        second = await sc.get_supabase()

    assert first is sentinel
    assert second is sentinel
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_supabase_le_credenciais_do_ambiente():
    """URL e key vêm de env (tests/conftest.py injeta os stubs)."""
    with patch.object(sc, "_supabase", None), \
         patch.object(sc, "acreate_client", new_callable=AsyncMock) as create:
        await sc.get_supabase()

    create.assert_awaited_once_with("https://test.supabase.co", "test-key")


def test_database_still_reexports_get_supabase():
    """21 testes fazem patch("app.database.get_supabase")."""
    from app import database

    assert database.get_supabase is sc.get_supabase
