import os
import logging
import httpx
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Configuración
# ------------------------------------------------------------------
RIOT_API_KEY = os.getenv("RIOT_API_KEY")
if not RIOT_API_KEY:
    logger.warning("RIOT_API_KEY no configurada. Las llamadas a Riot fallarán.")

# Mapeo de regiones del frontend a regiones de la API de Riot
REGION_MAP = {
    "LAN": "la1",      # Latinoamérica Norte
    "LAS": "la2",      # Latinoamérica Sur
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

# Mapeo de regiones a clusters para Account-V1
CLUSTER_MAP = {
    "la1": "americas",
    "la2": "americas",
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

# Cache para el mapeo de campeones
_CHAMPION_MAP = None

# ------------------------------------------------------------------
# Funciones auxiliares
# ------------------------------------------------------------------

async def _get_champion_map() -> Dict[int, str]:
    """Descarga y cachea el champion.json de Data Dragon."""
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
    """Realiza una llamada GET a la API de Riot con la clave de API."""
    if not RIOT_API_KEY:
        raise RuntimeError("RIOT_API_KEY no configurada")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {"X-Riot-Token": RIOT_API_KEY}
        resp = await client.get(url, headers=headers)
        
        if resp.status_code == 404:
            raise ValueError("Invocador no encontrado")
        elif resp.status_code == 403:
            raise PermissionError("Clave API inválida o sin permisos. Verifica tu RIOT_API_KEY.")
        elif resp.status_code == 429:
            raise RuntimeError("Demasiadas peticiones a la API de Riot. Espera un momento.")
        elif resp.status_code >= 400:
            raise RuntimeError(f"Error de Riot API: {resp.status_code} - {resp.text}")
        
        return resp.json()


# ------------------------------------------------------------------
# Funciones principales (SIN TAGLINE)
# ------------------------------------------------------------------

async def get_summoner_by_name(summoner_name: str, region_key: str) -> Dict[str, Any]:
    """
    Busca un invocador por nombre y región (SIN tagline).
    Usa la API de Riot de forma antigua (Summoner-V4 por nombre).
    """
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    
    # Summoner-V4 por nombre (más antiguo, pero no requiere tagline)
    url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-name/{summoner_name}"
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
    """Obtiene los campeones con mayor maestría."""
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")

    url = f"https://{region}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={count}"
    data = await _call_riot_api(url)
    return data


async def get_summoner_and_mastery(summoner_name: str, region_key: str) -> Dict[str, Any]:
    """
    Función principal que busca un invocador por nombre y región.
    Devuelve datos del invocador y sus top maestrías.
    """
    # 1. Obtener datos del invocador
    summoner_data = await get_summoner_by_name(summoner_name, region_key)
    puuid = summoner_data["puuid"]

    # 2. Obtener maestrías
    masteries = await get_top_masteries(puuid, region_key, count=6)

    # 3. Mapear IDs de campeones a claves
    champion_map = await _get_champion_map()
    top_champs = []
    for m in masteries:
        champion_id = m.get("championId")
        champ_key = champion_map.get(champion_id)
        if champ_key:
            top_champs.append({
                "id": champ_key,
                "championPoints": m.get("championPoints"),
                "championLevel": m.get("championLevel"),
                "masteryPoints": m.get("championPoints"),
                "masteryLevel": m.get("championLevel"),
            })
        else:
            logger.warning(f"ID de campeón desconocido: {champion_id}")

    # Respuesta final
    return {
        "name": summoner_data["name"],
        "region": region_key,
        "level": summoner_data["summonerLevel"],
        "iconId": summoner_data["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }