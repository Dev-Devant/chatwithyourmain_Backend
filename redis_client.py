import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None

CHAT_TTL_SECONDS = 30 * 60          # 30 minutos para historial
MAX_HISTORY_MESSAGES = 20

CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_TTL_SECONDS = 26 * 60 * 60   # 26 horas

SEARCH_RATE_LIMIT_MAX = 20
SEARCH_RATE_LIMIT_TTL_SECONDS = 24 * 60 * 60


# ========== INICIALIZACIÓN sin opciones problemáticas ==========

async def init_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL no configurada")

    _redis = redis.from_url(
        redis_url,
        decode_responses=True,
        socket_keepalive=True,         # Activa keepalive (usa valores por defecto del sistema)
        socket_timeout=5,              # Timeout para operaciones
        retry_on_timeout=True,         # Reintentar si timeout
        health_check_interval=30,      # Ping periódico para mantener viva la conexión
        max_connections=20,
    )

    await _redis.ping()
    logger.info("Conexión a Redis establecida")


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()
        _redis = None
        logger.info("Conexión a Redis cerrada")


# ========== CHAT HISTORY ==========

def _chat_key(puuid: str, champion_id: str) -> str:
    return f"chat:{puuid}:{champion_id}"


async def get_chat_history(puuid: str, champion_id: str) -> List[Dict[str, str]]:
    if not _redis:
        return []
    raw = await _redis.get(_chat_key(puuid, champion_id))
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("Historial corrupto en Redis, se descarta")
        return []


async def append_chat_messages(
    puuid: str,
    champion_id: str,
    user_message: str,
    champion_message: str,
) -> None:
    if not _redis:
        logger.warning("Redis no disponible, no se guarda historial")
        return
    key = _chat_key(puuid, champion_id)

    history = await get_chat_history(puuid, champion_id)
    history.append({"role": "user", "text": user_message})
    history.append({"role": "champion", "text": champion_message})
    history = history[-MAX_HISTORY_MESSAGES:]

    await _redis.set(key, json.dumps(history), ex=CHAT_TTL_SECONDS)


async def clear_chat_history(puuid: str, champion_id: str) -> None:
    if not _redis:
        return
    await _redis.delete(_chat_key(puuid, champion_id))


# ========== RATE LIMIT con script Lua (una sola llamada) ==========

RATE_LIMIT_LUA = """
    local key = KEYS[1]
    local ttl = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    local current = redis.call('INCR', key)
    if current == 1 then
        redis.call('EXPIRE', key, ttl)
    end
    local exceeded = 0
    if current > limit then
        exceeded = 1
    end
    return {current, exceeded}
"""

_LUA_RATE_LIMIT = None

async def _get_rate_limit_script():
    global _LUA_RATE_LIMIT
    if _LUA_RATE_LIMIT is None:
        if not _redis:
            return None
        _LUA_RATE_LIMIT = await _redis.register_script(RATE_LIMIT_LUA)
    return _LUA_RATE_LIMIT


async def check_and_increment_rate_limit(
    ip: str, prefix: str, max_requests: int, ttl_seconds: int
) -> Tuple[bool, int]:
    if not _redis:
        return True, 0

    key = f"ratelimit:{prefix}:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    script = await _get_rate_limit_script()
    if script is None:
        return True, 0

    try:
        current, exceeded = await script(keys=[key], args=[ttl_seconds, max_requests])
        allowed = (exceeded == 0)
        return allowed, current
    except Exception as e:
        logger.exception("Error ejecutando script de rate-limit")
        return True, 0


async def check_and_increment_chat_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(
        ip, "chat", CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_TTL_SECONDS
    )


async def check_and_increment_search_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(
        ip, "search", SEARCH_RATE_LIMIT_MAX, SEARCH_RATE_LIMIT_TTL_SECONDS
    )


async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    if not _redis:
        return {"used": 0, "limit": CHAT_RATE_LIMIT_MAX, "remaining": CHAT_RATE_LIMIT_MAX}

    key = f"ratelimit:chat:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    raw = await _redis.get(key)
    used = int(raw) if raw else 0
    used = min(used, CHAT_RATE_LIMIT_MAX)
    return {
        "used": used,
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }