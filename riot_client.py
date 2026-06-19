import os
import logging
import httpx
from typing import Dict, Any

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

# Routing CONTINENTAL (Account-V1, Match-V5)
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