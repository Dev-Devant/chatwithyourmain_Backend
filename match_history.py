import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import httpx

from riot import _call_riot_api, CONTINENT_MAP, _get_champion_map

logger = logging.getLogger(__name__)

DDRAGON_VERSION = "14.24.1"

_ITEM_MAP: Optional[Dict[int, str]] = None


async def _get_item_map() -> Dict[int, str]:
    """Mapea itemId -> nombre legible, usando Data Dragon."""
    global _ITEM_MAP
    if _ITEM_MAP is not None:
        return _ITEM_MAP
    url = f"https://ddragon.leagueoflegends.com/cdn/{DDRAGON_VERSION}/data/en_US/item.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    item_map = {int(item_id): item_data["name"] for item_id, item_data in data["data"].items()}
    _ITEM_MAP = item_map
    logger.info("Mapeo de items cargado (%d)", len(item_map))
    return item_map


async def get_match_ids(puuid: str, region_key: str, count: int = 20, queue_type: Optional[str] = None) -> List[str]:
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{continent}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}"
    if queue_type:
        url += f"&type={queue_type}"  # ej. "ranked"
    return await _call_riot_api(url)


async def get_match_detail(match_id: str, region_key: str) -> Dict[str, Any]:
    continent = CONTINENT_MAP.get(region_key)
    if not continent:
        raise ValueError(f"Región no soportada: {region_key}")
    url = f"https://{continent}.api.riotgames.com/lol/match/v5/matches/{match_id}"
    return await _call_riot_api(url)


def _extract_items(participant: Dict[str, Any], item_map: Dict[int, str]) -> List[str]:
    items = []
    for slot in range(7):  # item0..item5 + item6 (trinket)
        item_id = participant.get(f"item{slot}", 0)
        if item_id and item_id in item_map:
            items.append(item_map[item_id])
    return items


def _team_totals(participants: List[Dict[str, Any]], team_id: int) -> Dict[str, int]:
    kills = sum(p["kills"] for p in participants if p["teamId"] == team_id)
    deaths = sum(p["deaths"] for p in participants if p["teamId"] == team_id)
    assists = sum(p["assists"] for p in participants if p["teamId"] == team_id)
    return {"kills": kills, "deaths": deaths, "assists": assists}


async def build_match_summary(
    match_id: str,
    region_key: str,
    puuid: str,
    champ_map: Dict[int, str],
    item_map: Dict[int, str],
) -> Optional[Dict[str, Any]]:
    try:
        match = await get_match_detail(match_id, region_key)
    except Exception:
        logger.exception(f"No se pudo obtener el detalle del match {match_id}")
        return None

    info = match.get("info", {})
    participants = info.get("participants", [])

    me = next((p for p in participants if p.get("puuid") == puuid), None)
    if not me:
        return None

    # Rival de la misma línea (mismo teamPosition, equipo contrario).
    opponent = next(
        (
            p
            for p in participants
            if p.get("teamId") != me.get("teamId")
            and me.get("teamPosition")
            and p.get("teamPosition") == me.get("teamPosition")
        ),
        None,
    )

    my_team = _team_totals(participants, me["teamId"])
    enemy_team_id = 200 if me["teamId"] == 100 else 100
    enemy_team = _team_totals(participants, enemy_team_id)

    champion_id = me.get("championId")
    champion_name = champ_map.get(champion_id, me.get("championName", "Desconocido"))

    kills, deaths, assists = me.get("kills", 0), me.get("deaths", 0), me.get("assists", 0)
    kda = round((kills + assists) / deaths, 2) if deaths > 0 else float(kills + assists)

    game_creation_ms = info.get("gameCreation") or info.get("gameStartTimestamp")
    played_at = (
        datetime.fromtimestamp(game_creation_ms / 1000, tz=timezone.utc) if game_creation_ms else None
    )

    duration = info.get("gameDuration", 0) or 1
    cs = me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)

    return {
        "matchId": match_id,
        "playedAt": played_at.isoformat() if played_at else None,
        "queueId": info.get("queueId"),
        "gameDurationSeconds": info.get("gameDuration"),
        "win": me.get("win", False),
        "champion": champion_name,
        "championId": champion_id,
        "role": me.get("teamPosition") or me.get("individualPosition") or "DESCONOCIDO",
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": kda,
        "items": _extract_items(me, item_map),
        "goldEarned": me.get("goldEarned"),
        "totalDamageDealtToChampions": me.get("totalDamageDealtToChampions"),
        "visionScore": me.get("visionScore"),
        "csPerMin": round(cs / (duration / 60), 1),
        "myTeamKDA": my_team,
        "enemyTeamKDA": enemy_team,
        "opponent": (
            {
                "champion": champ_map.get(opponent.get("championId"), opponent.get("championName")),
                "kills": opponent.get("kills"),
                "deaths": opponent.get("deaths"),
                "assists": opponent.get("assists"),
            }
            if opponent
            else None
        ),
    }


async def get_recent_matches(puuid: str, region_key: str, count: int = 20) -> List[Dict[str, Any]]:
    match_ids = await get_match_ids(puuid, region_key, count=count)
    champ_map = await _get_champion_map()
    item_map = await _get_item_map()

    summaries = []
    for match_id in match_ids:
        summary = await build_match_summary(match_id, region_key, puuid, champ_map, item_map)
        if summary:
            summaries.append(summary)
    return summaries


def build_champion_recency(summaries: List[Dict[str, Any]]) -> Dict[str, str]:
    """Última fecha (ISO) en que se jugó cada campeón, dentro del historial traído."""
    last_seen: Dict[str, str] = {}
    for s in summaries:
        champ = s["champion"]
        if s["playedAt"] and (champ not in last_seen or s["playedAt"] > last_seen[champ]):
            last_seen[champ] = s["playedAt"]
    return last_seen


def flag_notable_games(summaries: List[Dict[str, Any]]) -> List[str]:
    """Notas en texto plano sobre partidas destacadas, para alimentar a la IA después."""
    notes = []
    for s in summaries:
        if s["deaths"] >= 8 and s["kills"] <= 1:
            opp = s["opponent"]["champion"] if s["opponent"] else "su rival de línea"
            notes.append(
                f"Partida mala con {s['champion']} ({s['kills']}/{s['deaths']}/{s['assists']}) "
                f"contra {opp} el {s['playedAt']}."
            )
        elif s["kda"] >= 5 and s["win"]:
            notes.append(
                f"Partida excelente con {s['champion']} ({s['kills']}/{s['deaths']}/{s['assists']}), "
                f"victoria el {s['playedAt']}."
            )
    return notes


def print_history_report(summoner_label: str, summaries: List[Dict[str, Any]]) -> None:
    print(f"\n========== Historial de {summoner_label} ==========")

    if not summaries:
        print("  (sin partidas recientes obtenidas)")
        print("=" * 50 + "\n")
        return

    for s in summaries:
        result = "VICTORIA" if s["win"] else "DERROTA"
        opp = (
            f" vs {s['opponent']['champion']} "
            f"({s['opponent']['kills']}/{s['opponent']['deaths']}/{s['opponent']['assists']})"
            if s["opponent"]
            else ""
        )
        print(
            f"[{s['playedAt']}] {s['champion']} ({s['role']}){opp} — "
            f"{s['kills']}/{s['deaths']}/{s['assists']} (KDA {s['kda']}) — {result} — "
            f"Build: {', '.join(s['items']) if s['items'] else 'sin items'} — "
            f"CS/min: {s['csPerMin']}"
        )
        print(
            f"    Team K/D/A: {s['myTeamKDA']['kills']}/{s['myTeamKDA']['deaths']}/{s['myTeamKDA']['assists']}  "
            f"vs  Enemy K/D/A: {s['enemyTeamKDA']['kills']}/{s['enemyTeamKDA']['deaths']}/{s['enemyTeamKDA']['assists']}"
        )

    print("\n--- Última vez jugado por campeón (dentro de este historial) ---")
    for champ, date in build_champion_recency(summaries).items():
        print(f"  {champ}: {date}")

    notes = flag_notable_games(summaries)
    if notes:
        print("\n--- Notas destacadas ---")
        for note in notes:
            print(f"  • {note}")

    print("=" * 50 + "\n")