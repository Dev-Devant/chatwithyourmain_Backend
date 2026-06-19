import logging
import time
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

from riot_client import _call_riot_api, REGION_MAP, CONTINENT_MAP, _get_champion_map
from match_history import get_recent_matches, build_player_context

logger = logging.getLogger(__name__)

# Caché simple en memoria: puuid -> (texto de contexto, timestamp en epoch).
# Vive mientras viva el proceso. Si reiniciás el server, se vacía (no pasa nada,
# se recalcula en la próxima búsqueda o chat).
_PLAYER_CONTEXT_CACHE: Dict[str, Tuple[str, float]] = {}
_CONTEXT_TTL_SECONDS = 10 * 60  # 10 minutos


async def get_account_by_riot_id(game_name: str, tag_line: str, region_key: str) -> Dict[str, Any]:
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")

    encoded_name = urllib.parse.quote(game_name)
    encoded_tag = urllib.parse.quote(tag_line)
    url = f"https://{continent}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
    return await _call_riot_api(url)


async def get_summoner_by_puuid(puuid: str, region_key: str) -> Dict[str, Any]:
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    return await _call_riot_api(url)


async def get_top_masteries(puuid: str, region_key: str, count: int = 6) -> List[Dict[str, Any]]:
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{region}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={count}"
    return await _call_riot_api(url)


async def _refresh_player_context(puuid: str, region_key: str) -> str:
    """Pega a Riot, recalcula el contexto y actualiza la caché."""
    try:
        matches = await get_recent_matches(puuid, region_key, count=10)
        context = build_player_context(matches)
    except Exception:
        logger.exception("No se pudo obtener el historial de partidas")
        context = "No se pudo obtener el historial de partidas de este jugador."

    _PLAYER_CONTEXT_CACHE[puuid] = (context, time.time())
    return context


async def get_cached_player_context(puuid: str, region_key: str) -> str:
    """
    Devuelve el contexto del jugador desde la caché si está fresco;
    si no existe o venció el TTL, lo recalcula contra Riot.
    """
    cached = _PLAYER_CONTEXT_CACHE.get(puuid)
    if cached:
        context, cached_at = cached
        if time.time() - cached_at < _CONTEXT_TTL_SECONDS:
            return context

    return await _refresh_player_context(puuid, region_key)


async def get_summoner_and_mastery(riot_id: str, region_key: str) -> Dict[str, Any]:
    if "#" not in riot_id:
        raise ValueError('Usa el formato "Nombre#TAG" (tu Riot ID completo).')
    game_name, tag_line = riot_id.split("#", 1)

    account = await get_account_by_riot_id(game_name, tag_line, region_key)
    puuid = account["puuid"]

    summoner = await get_summoner_by_puuid(puuid, region_key)
    masteries = await get_top_masteries(puuid, region_key, count=6)

    champ_map = await _get_champion_map()
    top_champs = []
    for m in masteries:
        champ_key = champ_map.get(m.get("championId"))
        if champ_key:
            top_champs.append({
                "id": champ_key,
                "championPoints": m.get("championPoints"),
                "championLevel": m.get("championLevel"),
                "masteryPoints": m.get("championPoints"),
                "masteryLevel": m.get("championLevel"),
            })
        else:
            logger.warning(f"ID de campeón no mapeado: {m.get('championId')}")

    # Calculamos y cacheamos el contexto ahora, así el primer mensaje del chat
    # ya lo encuentra fresco en la caché (no hace falta esperar al primer chat).
    await _refresh_player_context(puuid, region_key)

    return {
        "name": account.get("gameName", game_name),
        "tagLine": account.get("tagLine", tag_line),
        "region": region_key,
        "level": summoner["summonerLevel"],
        "iconId": summoner["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
        # Ya NO devolvemos playerContext acá — el cliente no lo necesita ni
        # debe poder verlo/mandarlo de vuelta.
    }