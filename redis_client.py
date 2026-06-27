import os
import json
import logging
import time
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)

CHAT_TTL_SECONDS = 30 * 60
MAX_HISTORY_MESSAGES = 20

CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_TTL_SECONDS = 26 * 60 * 60

SEARCH_RATE_LIMIT_MAX = 20
SEARCH_RATE_LIMIT_TTL_SECONDS = 24 * 60 * 60

# Caché en memoria (siempre útil)
_RATE_LIMIT_CACHE: Dict[str, Tuple[float, int]] = {}
_CACHE_TTL = 30  # segundos

REDIS_URL = os.getenv("REDIS_URL")


async def _get_redis_connection():
    """Crea una conexión efímera a Redis, sin mantenerla abierta."""
    if not REDIS_URL:
        raise RuntimeError("REDIS_URL no configurada")
    # Configuración mínima: sin keepalive, sin health check, sin retención
    return redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_keepalive=False,
        health_check_interval=0,
        socket_timeout=5,
        retry_on_timeout=False,
        max_connections=1,
    )


async def _execute_redis_command(operation):
    """Ejecuta una operación en Redis abriendo y cerrando conexión."""
    client = None
    try:
        client = await _get_redis_connection()
        return await operation(client)
    finally:
        if client:
            await client.close()


# ----- Funciones de historial (ahora con conexión efímera) -----

def _chat_key(puuid: str, champion_id: str) -> str:
    return f"chat:{puuid}:{champion_id}"


async def get_chat_history(puuid: str, champion_id: str) -> List[Dict[str, str]]:
    async def _get(client):
        raw = await client.get(_chat_key(puuid, champion_id))
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.exception("Historial corrupto en Redis, se descarta")
            return []
    return await _execute_redis_command(_get)


async def append_chat_messages(
    puuid: str,
    champion_id: str,
    user_message: str,
    champion_message: str,
) -> None:
    async def _append(client):
        history = await get_chat_history(puuid, champion_id)  # esto usa otra conexión, pero es aceptable
        history.append({"role": "user", "text": user_message})
        history.append({"role": "champion", "text": champion_message})
        history = history[-MAX_HISTORY_MESSAGES:]
        await client.set(_chat_key(puuid, champion_id), json.dumps(history), ex=CHAT_TTL_SECONDS)
    await _execute_redis_command(_append)


async def clear_chat_history(puuid: str, champion_id: str) -> None:
    async def _clear(client):
        await client.delete(_chat_key(puuid, champion_id))
    await _execute_redis_command(_clear)


# ----- Rate Limit con caché en memoria + Redis efímero -----

async def check_and_increment_rate_limit(ip: str, prefix: str, max_requests: int, ttl_seconds: int) -> Tuple[bool, int]:
    now = time.time()
    cache_key = f"{prefix}:{ip}"

    # 1. Caché en memoria (casi siempre se usará)
    if cache_key in _RATE_LIMIT_CACHE:
        ts, count = _RATE_LIMIT_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            new_count = count + 1
            _RATE_LIMIT_CACHE[cache_key] = (now, new_count)
            if new_count > max_requests:
                return False, new_count
            return True, new_count

    # 2. Si no está en caché, consultar Redis (conexión efímera)
    key = f"ratelimit:{prefix}:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    async def _rate_limit(client):
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_seconds)
        results = await pipe.execute()
        return results[0]  # contador actual

    try:
        current = await _execute_redis_command(_rate_limit)
    except Exception as e:
        logger.exception("Error en rate-limit con Redis")
        return True, 0  # permitir en caso de error

    _RATE_LIMIT_CACHE[cache_key] = (now, current)
    if current > max_requests:
        return False, current
    return True, current


async def check_and_increment_chat_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(
        ip, "chat", CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_TTL_SECONDS
    )


async def check_and_increment_search_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(
        ip, "search", SEARCH_RATE_LIMIT_MAX, SEARCH_RATE_LIMIT_TTL_SECONDS
    )


async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    """Obtiene el contador actual sin incrementar."""
    now = time.time()
    cache_key = f"chat:{ip}"

    if cache_key in _RATE_LIMIT_CACHE:
        ts, count = _RATE_LIMIT_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            used = min(count, CHAT_RATE_LIMIT_MAX)
            return {
                "used": used,
                "limit": CHAT_RATE_LIMIT_MAX,
                "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
            }

    # Si no está en caché, leer de Redis (conexión efímera)
    key = f"ratelimit:chat:{ip}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"

    async def _get_count(client):
        raw = await client.get(key)
        return int(raw) if raw else 0

    try:
        used = await _execute_redis_command(_get_count)
    except Exception:
        used = 0

    used = min(used, CHAT_RATE_LIMIT_MAX)
    _RATE_LIMIT_CACHE[cache_key] = (now, used)

    return {
        "used": used,
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }


# Para compatibilidad con el startup/shutdown, definimos funciones vacías
async def init_redis():
    logger.info("Redis configurado con conexiones efímeras (sin pool persistente)")


async def close_redis():
    logger.info("No hay conexión persistente que cerrar")