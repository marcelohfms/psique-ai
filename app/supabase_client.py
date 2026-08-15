"""Cliente Supabase compartilhado (singleton).

Módulo-folha: NÃO importa nada de `app.*`. Existe para que `app/database.py` e
`app/patients.py` compartilhem o cliente sem criar um ciclo de imports.
Ver docs/superpowers/specs/2026-08-14-quebrar-import-circular-patients-database-design.md
"""
import os

from supabase import AsyncClient, acreate_client

_supabase: AsyncClient | None = None


async def get_supabase() -> AsyncClient:
    global _supabase
    if _supabase is None:
        _supabase = await acreate_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_KEY"],
        )
    return _supabase
