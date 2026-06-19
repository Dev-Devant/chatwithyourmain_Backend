import logging
import urllib.parse
from typing import Dict, Any, List

from riot_client import _call_riot_api, REGION_MAP, CONTINENT_MAP, _get_champion_map
from match_history import get_recent_matches, print_history_report

logger = logging.getLogger(__name__)


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

    try:
        matches = await get_recent_matches(puuid, region_key, count=10)
        print_history_report(f"{account.get('gameName', game_name)}#{account.get('tagLine', tag_line)}", matches)
    except Exception:
        logger.exception("No se pudo obtener el historial de partidas")

    return {
        "name": account.get("gameName", game_name),
        "tagLine": account.get("tagLine", tag_line),
        "region": region_key,
        "level": summoner["summonerLevel"],
        "iconId": summoner["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }