import os
import json
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# No guardamos conexión global.

CHAT_TTL_SECONDS = 30 * 60  # 30 minutos
MAX_HISTORY_MESSAGES = 20

CHAT_RATE_LIMIT_MAX = 10
CHAT_RATE_LIMIT_TTL_SECONDS = 26 * 60 * 60

SEARCH_RATE_LIMIT_MAX = 20
SEARCH_RATE_LIMIT_TTL_SECONDS = 24 * 60 * 60

def _get_redis_url() -> str:
    url = os.getenv("REDIS_URL")
    if not url:
        raise RuntimeError("REDIS_URL no configurada")
    return url

# Funciones auxiliares que abren y cierran conexión cada vez

async def _get_redis_client():
    """Devuelve un cliente Redis que se conecta bajo demanda."""
    return redis.from_url(_get_redis_url(), decode_responses=True)

async def get_chat_history(puuid: str, champion_id: str) -> List[Dict[str, str]]:
    key = f"chat:{puuid}:{champion_id}"
    async with await _get_redis_client() as client:
        raw = await client.get(key)
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
    key = f"chat:{puuid}:{champion_id}"
    async with await _get_redis_client() as client:
        raw = await client.get(key)
        history = []
        if raw:
            try:
                history = json.loads(raw)
            except json.JSONDecodeError:
                history = []
        history.append({"role": "user", "text": user_message})
        history.append({"role": "champion", "text": champion_message})
        history = history[-MAX_HISTORY_MESSAGES:]
        await client.set(key, json.dumps(history), ex=CHAT_TTL_SECONDS)

async def clear_chat_history(puuid: str, champion_id: str) -> None:
    key = f"chat:{puuid}:{champion_id}"
    async with await _get_redis_client() as client:
        await client.delete(key)

def _rate_limit_key(ip: str, prefix: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"ratelimit:{prefix}:{ip}:{today}"

async def check_and_increment_rate_limit(ip: str, prefix: str, max_requests: int, ttl_seconds: int) -> Tuple[bool, int]:
    key = _rate_limit_key(ip, prefix)
    async with await _get_redis_client() as client:
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, ttl_seconds)
        if current > max_requests:
            return False, current
        return True, current

async def check_and_increment_chat_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "chat", CHAT_RATE_LIMIT_MAX, CHAT_RATE_LIMIT_TTL_SECONDS)

async def get_chat_limit_status(ip: str) -> Dict[str, int]:
    key = _rate_limit_key(ip, "chat")
    async with await _get_redis_client() as client:
        raw = await client.get(key)
        used = int(raw) if raw else 0
    return {
        "used": min(used, CHAT_RATE_LIMIT_MAX),
        "limit": CHAT_RATE_LIMIT_MAX,
        "remaining": max(0, CHAT_RATE_LIMIT_MAX - used),
    }

async def check_and_increment_search_limit(ip: str) -> Tuple[bool, int]:
    return await check_and_increment_rate_limit(ip, "search", SEARCH_RATE_LIMIT_MAX, SEARCH_RATE_LIMIT_TTL_SECONDS)