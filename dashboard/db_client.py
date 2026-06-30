"""Getter lazy do cliente Supabase para os módulos do painel da atendente.

Mantido separado de main.py para evitar import circular (main importa as rotas,
as rotas importam a camada de dados, que precisa do cliente).
"""
import os
from supabase import AsyncClient, acreate_client

_client: AsyncClient | None = None


async def get_client() -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _client
