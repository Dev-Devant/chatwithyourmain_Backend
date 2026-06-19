import os
import logging
import httpx
import urllib.parse
from typing import Dict, Any, List, Optional
from match_history import get_recent_matches, print_history_report


logger = logging.getLogger(__name__)

RIOT_API_KEY = os.getenv("RIOT_API_KEY")
if not RIOT_API_KEY:
    logger.warning("RIOT_API_KEY no configurada")

# Routing de PLATAFORMA (Summoner-V4, Champion-Mastery-V4)
REGION_MAP = {
    "LAN": "la1",
    "LAS": "la2",
    "NA": "na1",
    "EUW": "euw1",
    "EUNE": "eun1",
    "KR": "kr",
    "BR": "br1",
    "OCE": "oc1",
    "JP": "jp1",
    "RU": "ru",
    "TR": "tr1",
}

# Routing CONTINENTAL (Account-V1) — agrupa varias plataformas
CONTINENT_MAP = {
    "LAN": "americas",
    "LAS": "americas",
    "NA": "americas",
    "BR": "americas",
    "OCE": "americas",
    "EUW": "europe",
    "EUNE": "europe",
    "TR": "europe",
    "RU": "europe",
    "KR": "asia",
    "JP": "asia",
}

_CHAMPION_MAP = None


async def _get_champion_map() -> Dict[int, str]:
    global _CHAMPION_MAP
    if _CHAMPION_MAP is not None:
        return _CHAMPION_MAP
    url = "https://ddragon.leagueoflegends.com/cdn/14.24.1/data/en_US/champion.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    champion_map = {int(c["key"]): key for key, c in data["data"].items()}
    _CHAMPION_MAP = champion_map
    logger.info("Mapeo de campeones cargado (%d)", len(champion_map))
    return champion_map


async def _call_riot_api(url: str) -> Dict[str, Any]:
    if not RIOT_API_KEY:
        raise RuntimeError("RIOT_API_KEY no configurada")
    async with httpx.AsyncClient() as client:
        headers = {"X-Riot-Token": RIOT_API_KEY}
        resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            raise ValueError("Invocador no encontrado")
        elif resp.status_code == 403:
            raise PermissionError("Clave API inválida, expirada o sin permisos para este endpoint")
        elif resp.status_code == 429:
            raise RuntimeError("Rate limit excedido. Espera un momento.")
        elif resp.status_code >= 400:
            raise RuntimeError(f"Error Riot API: {resp.status_code} - {resp.text}")
        return resp.json()


async def get_account_by_riot_id(game_name: str, tag_line: str, region_key: str) -> Dict[str, Any]:
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")

    encoded_name = urllib.parse.quote(game_name)
    encoded_tag = urllib.parse.quote(tag_line)
    url = f"https://{continent}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}"
    return await _call_riot_api(url)  # {"puuid", "gameName", "tagLine"}


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
    """
    riot_id debe venir como "gameName#tagLine".
    """
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

    return {
        "name": account.get("gameName", game_name),
        "tagLine": account.get("tagLine", tag_line),
        "region": region_key,
        "level": summoner["summonerLevel"],
        "iconId": summoner["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }


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

    # --- NUEVO: historial de partidas, por ahora solo a consola ---
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