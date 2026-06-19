import os
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from riot import get_summoner_and_mastery, REGION_MAP, get_cached_player_info
from ia import get_ai_response
from db import init_db, close_db, save_summoner, list_summoners
from redis_client import init_redis, close_redis, get_chat_history, append_chat_messages


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-chat")

app = FastAPI(title="AI Chat Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await init_db()
    await init_redis()

@app.on_event("shutdown")
async def shutdown():
    await close_db()
    await close_redis()
# =========================
# Modelos
# =========================


class SummonerRequest(BaseModel):
    summoner_name: str
    region: str


class ChatHistoryItem(BaseModel):
    role: str  # "user" | "champion"
    text: str

class ChatRequest(BaseModel):
    championId: str
    championName: str
    championTitle: str
    persona: str
    message: str
    puuid: str
    region: str

# =========================
# Endpoints
# =========================
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/summoner")
async def post_summoner(request: SummonerRequest):
    return await _handle_summoner(request.summoner_name, request.region)


@app.get("/api/summoner")
async def get_summoner(
    summoner_name: str = Query(..., description="Riot ID completo, ej. Nombre#TAG"),
    region: str = Query(..., description="Región (LAN, NA, EUW, ...)")
):
    return await _handle_summoner(summoner_name, region)


async def _handle_summoner(summoner_name: str, region: str):
    try:
        if region not in REGION_MAP:
            raise HTTPException(status_code=400, detail="Región no soportada")
        result = await get_summoner_and_mastery(summoner_name, region)

        # Guardamos/actualizamos el summoner en la base
        await save_summoner(
            puuid=result["puuid"],
            game_name=result["name"],
            tag_line=result["tagLine"],
            region=result["region"],
            icon_id=result["iconId"],
            level=result["level"],
        )

        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception:
        logger.exception("Error en /api/summoner")
        raise HTTPException(status_code=500, detail="Error interno del servidor")



@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío")

    if request.region not in REGION_MAP:
        raise HTTPException(status_code=400, detail="Región no soportada")

    # Historial desde Redis en vez del front
    history = await get_chat_history(request.puuid, request.championId)

    player_info = await get_cached_player_info(request.puuid, request.region)

    text = await get_ai_response(
        champion_name=request.championName,
        champion_title=request.championTitle,
        persona=request.persona,
        history=history,
        message=request.message,
        summoner_name=player_info["name"],
        player_context=player_info["context"],
    )

    # Guardamos el intercambio en Redis (con su TTL)
    await append_chat_messages(
        puuid=request.puuid,
        champion_id=request.championId,
        user_message=request.message,
        champion_message=text,
    )

    return {"text": text}
    
@app.get("/api/summoners")
async def get_summoners(limit: int = 50):
    return await list_summoners(limit)
    
@app.get("/api/chat/history")
async def chat_history(puuid: str, championId: str):
    return await get_chat_history(puuid, championId)


@app.delete("/api/chat/history")
async def delete_chat_history(puuid: str, championId: str):
    await clear_chat_history(puuid, championId)
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)