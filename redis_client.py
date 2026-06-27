import os
import json
import logging
import time
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

# Caché en memoria para rate limits (evita llamar a Redis en cada petición)
# Estructura: {f"{prefix}:{ip}": (timestamp, count)}
_RATE_LIMIT_MEMORY: Dict[str, Tuple[float, int]] = {}
_RATE_LIMIT_MEMORY_TTL = 2  # segundos


# ========== INICIALIZACIÓN ==========

async def init_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL no configurada")
    _redis = redis.from_url(redis_url, decode_responses=True)
    await _redis.ping()
    logger.info("Conexión a Redis establecida")
    return _redis


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

    # Leer historial actual
    history = await get_chat_history(puuid, champion_id)
    history.append({"role": "user", "text": user_message})
    history.append({"role": "champion", "text": champion_message})
    history = history[-MAX_HISTORY_MESSAGES:]

    # Guardar de una vez (un solo SET con TTL)
    await _redis.set(key, json.dumps(history), ex=CHAT_TTL_SECONDS)


async def clear_chat_history(puuid: str, champion_id: str) -> None:
    if not _redis:
        return
    await _redis.delete(_chat_key(puuid, champion_id))


# ========== RATE LIMITS (con caché en memoria) ==========

def _rate_limit_key(ip: str, prefix: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ratelimit:{prefix}:{ip}:{today}"


async def check_and_increment_rate_limit(ip: str, prefix: str, max_requests: int, ttl_seconds: int) -> Tuple[bool, int]:
    """Devuelve (permitido, contador_actual) usando caché en memoria + Redis."""
    now = time.time()
    cache_key = f"{prefix}:{ip}"

    # 1. ¿Tenemos un valor en memoria y aún no ha expirado?
    if cache_key in _RATE_LIMIT_MEMORY:
        ts, count = _RATE_LIMIT_MEMORY[cache_key]
        if now - ts < _RATE_LIMIT_MEMORY_TTL:
            # Incrementar localmente
            new_count = count + 1
            _RATE_LIMIT_MEMORY[cache_key] = (now, new_count)
            if new_count > max_requests:
                return False, new_count
            return True, new_count

    # 2. Si no está en caché o expiró, leer/actualizar desde Redis (con pipeline)
    if not _redis:
        # Sin Redis, permitimos todo (modo inseguro, pero para desarrollo)
        return True, 0

    key = _rate_limit_key(ip, prefix)
    pipe = _redis.pipeline()
    pipe.incr(key)
    pipe.expire(key, ttl_seconds)
    results = await pipe.execute()
    current = results[0]  # valor después de incr

    # Guardar en memoria con el timestamp actual
    _RATE_LIMIT_MEMORY[cache_key] = (now, current)

    if current > max_requests:
        return False, current
    return True, current


async def check_and_increment_chat_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "chat", CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_TTL_SECONDS)


async def check_and_increment_search_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "search", SEARCH_RATE_LIMIT_MAX, SEARCH_RATE_LIMIT_TTL_SECONDS)


async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    """Devuelve el estado actual del límite de chat, usando caché en memoria si es posible."""
    now = time.time()
    cache_key = f"chat:{ip}"

    # Intentar leer de memoria
    if cache_key in _RATE_LIMIT_MEMORY:
        ts, count = _RATE_LIMIT_MEMORY[cache_key]
        if now - ts < _RATE_LIMIT_MEMORY_TTL:
            used = min(count, CHAT_RATE_LIMIT_MAX)
            return {
                "used": used,
                "limit": CHAT_RATE_LIMIT_MAX,
                "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
            }

    # Si no, leer de Redis
    if not _redis:
        return {"used": 0, "limit": CHAT_RATE_LIMIT_MAX, "remaining": CHAT_RATE_LIMIT_MAX}

    key = _rate_limit_key(ip, "chat")
    raw = await _redis.get(key)
    used = int(raw) if raw else 0
    used = min(used, CHAT_RATE_LIMIT_MAX)

    # Guardar en memoria para próximas consultas
    _RATE_LIMIT_MEMORY[cache_key] = (now, used)

    return {
        "used": used,
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }