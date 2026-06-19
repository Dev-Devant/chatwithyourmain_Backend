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
    "LAN": "lan1",
    "LAS": "las1",
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

# Mapeo de regiones a rutas de la API (para Account-V1 usamos americas, europe, asia, etc.)
# Según la región, elegimos el cluster correcto.
CLUSTER_MAP = {
    "lan1": "americas",
    "las1": "americas",
    "na1": "americas",
    "br1": "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1": "europe",
    "ru": "europe",
    "kr": "asia",
    "jp1": "asia",
    "oc1": "asia",  # OCE suele ir en americas? En realidad OCE está en Americas, pero lo dejamos así.
}
# Nota: para OCE, el cluster es americas, pero la región es oc1.
# Ajustamos:
CLUSTER_MAP["oc1"] = "americas"

# Cache para el mapeo de campeones (se carga una vez)
_CHAMPION_MAP = None

# ------------------------------------------------------------------
# Funciones auxiliares
# ------------------------------------------------------------------

async def _get_champion_map() -> Dict[int, str]:
    """
    Descarga y cachea el champion.json de Data Dragon para mapear IDs numéricos a claves.
    """
    global _CHAMPION_MAP
    if _CHAMPION_MAP is not None:
        return _CHAMPION_MAP

    url = "https://ddragon.leagueoflegends.com/cdn/14.24.1/data/en_US/champion.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    # Construir mapeo: ID numérico -> clave (ej. 1 -> "Annie")
    champion_map = {}
    for key, champ_data in data["data"].items():
        champion_map[int(champ_data["key"])] = key
    _CHAMPION_MAP = champion_map
    logger.info("Mapeo de campeones cargado (%d campeones)", len(champion_map))
    return champion_map


async def _call_riot_api(url: str, region: str) -> Dict[str, Any]:
    """
    Realiza una llamada GET a la API de Riot con la clave de API.
    Lanza excepción si el código de estado no es 2xx.
    """
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
# Funciones principales
# ------------------------------------------------------------------

async def get_puuid_by_riot_id(game_name: str, tag_line: str, region_key: str) -> str:
    """
    Obtiene el PUUID de un invocador usando Account-V1.
    region_key: "LAN", "NA", etc.
    """
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")
    cluster = CLUSTER_MAP.get(region, "americas")  # Por defecto americas

    # Riot ID puede contener caracteres especiales, codificamos URL
    url = f"https://{cluster}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    data = await _call_riot_api(url, region)
    return data.get("puuid")


async def get_summoner_by_puuid(puuid: str, region_key: str) -> Dict[str, Any]:
    """
    Obtiene los datos del invocador (nivel, icono, summonerId) usando Summoner-V4.
    """
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")

    url = f"https://{region}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
    data = await _call_riot_api(url, region)
    return {
        "id": data.get("id"),
        "accountId": data.get("accountId"),
        "puuid": data.get("puuid"),
        "name": data.get("name"),
        "profileIconId": data.get("profileIconId"),
        "summonerLevel": data.get("summonerLevel"),
    }


async def get_top_masteries(puuid: str, region_key: str, count: int = 6) -> List[Dict[str, Any]]:
    """
    Obtiene los campeones con mayor maestría.
    Devuelve lista con {championId, championPoints, championLevel, ...}
    """
    region = REGION_MAP.get(region_key)
    if not region:
        raise ValueError(f"Región no soportada: {region_key}")

    url = f"https://{region}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top?count={count}"
    data = await _call_riot_api(url, region)
    # data es una lista
    return data


async def get_summoner_and_mastery(game_name: str, tag_line: str, region_key: str) -> Dict[str, Any]:
    """
    Función principal que combina todo:
    - Obtiene PUUID
    - Obtiene datos del invocador
    - Obtiene top maestrías
    - Mapea IDs de campeones a nombres
    """
    # 1. PUUID
    puuid = await get_puuid_by_riot_id(game_name, tag_line, region_key)

    # 2. Datos del invocador
    summoner_data = await get_summoner_by_puuid(puuid, region_key)

    # 3. Maestrías
    masteries = await get_top_masteries(puuid, region_key, count=6)

    # 4. Mapear IDs de campeones a claves
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
                "masteryPoints": m.get("championPoints"),  # alias para el frontend
                "masteryLevel": m.get("championLevel"),
            })
        else:
            logger.warning(f"ID de campeón desconocido: {champion_id}")

    # Respuesta final
    return {
        "name": summoner_data["name"],
        "tag": tag_line,
        "region": region_key,
        "level": summoner_data["summonerLevel"],
        "iconId": summoner_data["profileIconId"],
        "puuid": puuid,
        "topChampions": top_champs,
    }