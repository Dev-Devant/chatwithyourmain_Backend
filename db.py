import os
import logging
import asyncpg
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

pool: Optional[asyncpg.Pool] = None


async def init_db():
    global pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL no configurada")

    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=10,
        command_timeout=10,
        timeout=10
    )

    await _ensure_schema()
    logger.info("Pool de Postgres inicializado")


async def close_db():
    global pool
    if pool:
        await pool.close()


def get_pool() -> asyncpg.Pool:
    return pool


async def _ensure_schema():
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS summoners (
                puuid TEXT PRIMARY KEY,
                game_name TEXT NOT NULL,
                tag_line TEXT NOT NULL,
                region TEXT NOT NULL,
                icon_id INT,
                level INT,
                search_count INT NOT NULL DEFAULT 1,
                first_searched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_searched_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )


# ---------- SUMMONERS ----------

async def save_summoner(
    puuid: str,
    game_name: str,
    tag_line: str,
    region: str,
    icon_id: Optional[int] = None,
    level: Optional[int] = None,
) -> None:
    """Inserta el summoner si es nuevo, o actualiza su info + contador si ya existía."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO summoners (puuid, game_name, tag_line, region, icon_id, level)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (puuid) DO UPDATE SET
                game_name = EXCLUDED.game_name,
                tag_line = EXCLUDED.tag_line,
                region = EXCLUDED.region,
                icon_id = EXCLUDED.icon_id,
                level = EXCLUDED.level,
                search_count = summoners.search_count + 1,
                last_searched_at = now();
            """,
            puuid, game_name, tag_line, region, icon_id, level,
        )


async def list_summoners(limit: int = 50) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT puuid, game_name, tag_line, region, icon_id, level,
                   search_count, first_searched_at, last_searched_at
            FROM summoners
            ORDER BY last_searched_at DESC
            LIMIT $1;
            """,
            limit,
        )
        return [dict(r) for r in rows]