import logging
import time
import urllib.parse
from typing import Dict, Any, List, Optional

from riot_client import _call_riot_api, REGION_MAP, CONTINENT_MAP, _get_champion_map
from match_history import get_recent_matches, build_player_context

logger = logging.getLogger(__name__)

# Caché en memoria: puuid -> {"name": str, "context": str, "cached_at": float}
_PLAYER_CACHE: Dict[str, Dict[str, Any]] = {}
_CONTEXT_TTL_SECONDS = 10 * 60  # 10 minutos


async def get_account_by_riot_id(game_name: str, tag_line: str, region_key: str) -> Dict[str, Any]:
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")

    encoded_name = urllib.parse.quote(game_name)
    encoded_tag = urllib.parse.quote(tag_line)
    url = f"https://{continent}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
    return await _call_riot_api(url)


async def get_account_by_puuid(puuid: str, region_key: str) -> Dict[str, Any]:
    """Account-V1 también tiene lookup inverso por puuid -> gameName/tagLine."""
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{continent}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}"
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


async def _refresh_player_cache(
    puuid: str, region_key: str, known_name: Optional[str] = None
) -> Dict[str, Any]:
    """Recalcula nombre + contexto y actualiza la caché para este puuid."""
    name = known_name
    if not name:
        try:
            account = await get_account_by_puuid(puuid, region_key)
            name = account.get("gameName", "invocador")
        except Exception:
            logger.exception("No se pudo resolver el nombre del invocador por puuid")
            name = "invocador"

    try:
        matches = await get_recent_matches(puuid, region_key, count=10)
        context = build_player_context(matches)
    except Exception:
        logger.exception("No se pudo obtener el historial de partidas")
        context = "No se pudo obtener el historial de partidas de este jugador."

    entry = {"name": name, "context": context, "cached_at": time.time()}
    _PLAYER_CACHE[puuid] = entry
    return entry


async def get_cached_player_info(puuid: str, region_key: str) -> Dict[str, Any]:
    """
    Devuelve {"name", "context"} desde la caché si está fresca; si no existe
    o venció el TTL, lo recalcula contra Riot.
    """
    cached = _PLAYER_CACHE.get(puuid)
    if cached and (time.time() - cached["cached_at"] < _CONTEXT_TTL_SECONDS):
        return cached
    return await _refresh_player_cache(puuid, region_key)


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

    # Precalentamos la caché ya con el nombre que conocemos (evita una llamada extra a Riot).
    await _refresh_player_cache(puuid, region_key, known_name=account.get("gameName", game_name))

    return {
        "name": account.get("gameName", game_name),
        "tagLine": account.get("tagLine", tag_line),
        "region": region_key,
        "level": summoner["summonerLevel"],
        "iconId": summoner["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }