import os
import logging
import httpx
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Clave de API
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
if not RIOT_API_KEY:
    logger.warning("RIOT_API_KEY no configurada. Las llamadas a Riot fallarán.")

# Mapeo de regiones del frontend a regiones de la API de Riot
REGION_MAP = {
    "LAN": "la1",
    "LAS": "la1",   # Nota: LAS y LAN comparten el mismo cluster de juego (la1)
    "NA": "na1",
    "EUW": "euw1",
    "EUNE": "eun1",
    "KR": "kr",
    "BR": "br1",
    "OCE": "oc1",
    "JP": "jp1",
    "RU": "ru",
    "TR": "tr1"
}

# Para Account-V1, se necesita el cluster (americas, europe, asia)
CLUSTER_MAP = {
    "la1": "americas",
    "na1": "americas",
    "br1": "americas",
    "oc1": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
}

# Cache de campeones
_CHAMPION_MAP = None

# ------------------------------------------------------------------
# Funciones auxiliares
# ------------------------------------------------------------------

async def _get_champion_map() -> Dict[int, str]:
    global _CHAMPION_MAP
    if _CHAMPION_MAP is not None:
        return _CHAMPION_MAP

    url = "https://ddragon.leagueoflegends.com/cdn/14.24.1/data/en_US/champion.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    champion_map = {}
    for key, champ_data in data["data"].items():
        champion_map[int(champ_data["key"])] = key
    _CHAMPION_MAP = champion_map
    logger.info("Mapeo de campeones cargado (%d campeones)", len(champion_map))
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
            raise PermissionError("Clave API inválida o sin permisos")
        elif resp.status_code == 429:
            raise RuntimeError("Demasiadas peticiones a la API de Riot. Espera un momento.")
        elif resp.status_code >= 400:
            raise RuntimeError(f"Error de Riot API: {resp.status_code} - {resp.text}")
        return resp.json()


# ------------------------------------------------------------------
# Búsqueda por nombre (sin tagline) - usando Summoner-V4
# ------------------------------------------------------------------
async def get_summoner_by_name(summoner_name: str, region_key: str) -> Dict[str, Any]:
    """
    Obtiene datos del invocador usando el endpoint clásico por nombre (sin tagline).
    """
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")

    # El nombre debe estar codificado para URL (espacios, etc.)
    encoded_name = httpx.quote(summoner_name)
    url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-name/{encoded_name}"
    data = await _call_riot_api(url)
    return {
        "id": data.get("id"),
        "accountId": data.get("accountId"),
        "puuid": data.get("puuid"),
        "name": data.get("name"),
        "profileIconId": data.get("profileIconId"),
        "summonerLevel": data.get("summonerLevel"),
    }


# ------------------------------------------------------------------
# Búsqueda por Riot ID (con tagline) - usando Account-V1
# ------------------------------------------------------------------
async def get_puuid_by_riot_id(game_name: str, tag_line: str, region_key: str) -> str:
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    cluster = CLUSTER_MAP.get(region, "americas")
    url = f"https://{cluster}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    data = await _call_riot_api(url)
    return data.get("puuid")


async def get_summoner_by_puuid(puuid: str, region_key: str) -> Dict[str, Any]:
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    data = await _call_riot_api(url)
    return {
        "id": data.get("id"),
        "accountId": data.get("accountId"),
        "puuid": data.get("puuid"),
        "name": data.get("name"),
        "profileIconId": data.get("profileIconId"),
        "summonerLevel": data.get("summonerLevel"),
    }


async def get_top_masteries(puuid: str, region_key: str, count: int = 6) -> List[Dict[str, Any]]:
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{region}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={count}"
    data = await _call_riot_api(url)
    return data


# ------------------------------------------------------------------
# Función principal unificada
# ------------------------------------------------------------------
async def get_summoner_and_mastery(game_name: str, tag_line: Optional[str], region_key: str) -> Dict[str, Any]:
    """
    Busca invocador. Si se proporciona tag_line, usa Account-V1; si no, usa Summoner-V4 por nombre.
    """
    # Si no hay tagline, usar búsqueda por nombre (método antiguo)
    if not tag_line or tag_line.strip() == "":
        logger.info(f"Buscando por nombre sin tagline: {game_name} en {region_key}")
        summoner_data = await get_summoner_by_name(game_name, region_key)
        puuid = summoner_data["puuid"]
    else:
        # Primero obtener PUUID con Account-V1
        puuid = await get_puuid_by_riot_id(game_name, tag_line, region_key)
        # Luego obtener el resto de datos del invocador con Summoner-V4
        summoner_data = await get_summoner_by_puuid(puuid, region_key)

    # Obtener maestrías
    masteries = await get_top_masteries(puuid, region_key, count=6)

    # Mapear IDs de campeones
    champion_map = await _get_champion_map()
    top_champs = []
    for m in masteries:
        champion_id = m.get("championId")
        champ_key = champion_map.get(champion_id)
        if champ_key:
            top_champs.append({
                "id": champ_key,
                "masteryLevel": m.get("championLevel"),
                "masteryPoints": m.get("championPoints"),
            })

    return {
        "name": summoner_data["name"],
        "tag": tag_line or "",  # Si no se proporcionó, devolver vacío
        "region": region_key,
        "level": summoner_data["summonerLevel"],
        "iconId": summoner_data["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }