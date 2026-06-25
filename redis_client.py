import os
import json
import logging
from typing import List, Dict, Optional
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None

CHAT_TTL_SECONDS = 30 * 60  # 30 minutos de inactividad y se borra
MAX_HISTORY_MESSAGES = 20   # tope de mensajes guardados por chat

CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_TTL_SECONDS = 26 * 60 * 60 # margen extra sobre 24h para no cortar justo a la medianoche

async def init_redis():
    global _redis
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning("REDIS_URL no configurada")
    _redis = redis.from_url(redis_url, decode_responses=True)
    await _redis.ping()
    logger.info("Conexión a Redis establecida")


async def close_redis():
    global _redis
    if _redis:
        await _redis.close()


def _chat_key(puuid: str, champion_id: str) -> str:
    return f"chat:{puuid}:{champion_id}"


async def get_chat_history(puuid: str, champion_id: str) -> List[Dict[str, str]]:
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
    key = _chat_key(puuid, champion_id)
    history = await get_chat_history(puuid, champion_id)

    history.append({"role": "user", "text": user_message})
    history.append({"role": "champion", "text": champion_message})

    # Recortamos para no acumular infinito
    history = history[-MAX_HISTORY_MESSAGES:]

    await _redis.set(key, json.dumps(history), ex=CHAT_TTL_SECONDS)


async def clear_chat_history(puuid: str, champion_id: str) -> None:
    await _redis.delete(_chat_key(puuid, champion_id))


def _rate_limit_key(ip: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ratelimit:chat:{ip}:{today}"

async def check_and_increment_chat_limit(ip: str) -> tuple[bool, int]:
    """
    Incrementa el contador de chats del día para esta IP.
    Devuelve (permitido, usados_hasta_ahora).
    """
    key = _rate_limit_key(ip)
    current = await _redis.incr(key)
    if current == 1:
        # Solo seteamos el TTL la primera vez que se crea la key
        await _redis.expire(key, CHAT_RATE_LIMIT_TTL_SECONDS)

    if current > CHAT_RATE_LIMIT_MAX:
        return False, current
    return True, current


async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    """Para que el frontend pueda mostrar cuántos mensajes quedan, sin consumir cupo."""
    key = _rate_limit_key(ip)
    raw = await _redis.get(key)
    used = int(raw) if raw else 0
    return {
        "used": min(used, CHAT_RATE_LIMIT_MAX),
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }
