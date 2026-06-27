import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None

CHAT_TTL_SECONDS = 30 * 60  # 30 minutos
MAX_HISTORY_MESSAGES = 20

CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_TTL_SECONDS = 26 * 60 * 60

SEARCH_RATE_LIMIT_MAX = 20
SEARCH_RATE_LIMIT_TTL_SECONDS = 24 * 60 * 60

# ========== INICIALIZACIÓN ==========

async def init_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL no configurada")
    # SOLO la conexión, sin ningún tipo de latido o keepalive extra
    _redis = redis.from_url(redis_url, decode_responses=True)
    await _redis.ping()  # Déjalo para verificar que la contraseña/URL es correcta al inicio
    logger.info("Conexión a Redis establecida")
    return _redis

async def close_redis():
    """Cierra la conexión a Redis."""
    global _redis
    if _redis:
        await _redis.close()
        _redis = None
        logger.info("Conexión a Redis cerrada")

# ========== FUNCIONES EXISTENTES ==========

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

def _rate_limit_key(ip: str, prefix: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ratelimit:{prefix}:{ip}:{today}"

async def check_and_increment_rate_limit(ip: str, prefix: str, max_requests: int, ttl_seconds: int) -> Tuple[bool, int]:
    if not _redis:
        return True, 0  # Si no hay Redis, permitir todo (modo inseguro)
    key = _rate_limit_key(ip, prefix)
    current = await _redis.incr(key)
    if current == 1:
        await _redis.expire(key, ttl_seconds)
    if current > max_requests:
        return False, current
    return True, current

async def check_and_increment_chat_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "chat", CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_TTL_SECONDS)

async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    if not _redis:
        return {"used": 0, "limit": CHAT_RATE_LIMIT_MAX, "remaining": CHAT_RATE_LIMIT_MAX}
    key = _rate_limit_key(ip, "chat")
    raw = await _redis.get(key)
    used = int(raw) if raw else 0
    return {
        "used": min(used, CHAT_RATE_LIMIT_MAX),
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }

async def check_and_increment_search_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "search", SEARCH_RATE_LIMIT_MAX, SEARCH_RATE_LIMIT_TTL_SECONDS)