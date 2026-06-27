import os
import logging
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from typing import Optional, Tuple
import jwt
from datetime import datetime, timedelta, timezone
import secrets
from time import time

from riot import get_summoner_and_mastery, REGION_MAP, get_cached_player_info
from riot_client import _get_champion_map
from ia import get_ai_response
from db import init_db, close_db, save_summoner
from redis_client import (
    init_redis, close_redis,
    get_chat_history, append_chat_messages, clear_chat_history,
    check_and_increment_chat_limit, check_and_increment_search_limit,
    get_chat_limit_status
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-chat")

app = FastAPI(title="AI Chat Backend", version="1.0.0")

# ==================== CORS ====================
ALLOWED_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,https://chatwithyourmain.andresrosalez.dev").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== JWT (solo para historial) ====================
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    logger.warning("JWT_SECRET no configurada, usando valor aleatorio")
    JWT_SECRET = secrets.token_urlsafe(32)
else:
    logger.info("JWT_SECRET configurada desde entorno")

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24

def create_token(puuid: str, region: str) -> str:
    payload = {
        "puuid": puuid,
        "region": region,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRATION_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

async def get_puuid_from_token(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
    token = credentials.credentials
    payload = decode_token(token)
    puuid = payload.get("puuid")
    region = payload.get("region")
    if not puuid or not region:
        raise HTTPException(status_code=401, detail="Token mal formado")
    return puuid, region

# ==================== Modelos ====================
class SummonerRequest(BaseModel):
    summoner_name: str = Field(..., max_length=100)
    region: str = Field(..., max_length=10)

class ChatRequest(BaseModel):
    championId: str = Field(..., max_length=50)
    championName: Optional[str] = None
    championTitle: Optional[str] = None
    persona: str = Field(..., max_length=500)
    message: str = Field(..., max_length=500)
    puuid: str = Field(..., max_length=100)
    region: str = Field(..., max_length=10)

# ==================== Eventos ====================
@app.on_event("startup")
async def startup():
    await init_db()
    await init_redis()
    await _get_champion_map()

@app.on_event("shutdown")
async def shutdown():
    await close_db()
    await close_redis()

# ==================== Funciones auxiliares ====================
def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

async def _resolve_champion_id(champion_id: str) -> Tuple[int, str]:
    champion_map = await _get_champion_map()  # {int: nombre}
    name_to_id = {nombre: id_num for id_num, nombre in champion_map.items()}
    try:
        num_id = int(champion_id)
        if num_id in champion_map:
            return num_id, champion_map[num_id]
    except ValueError:
        pass
    if champion_id in name_to_id:
        num_id = name_to_id[champion_id]
        return num_id, champion_id
    raise ValueError(f"Campeón no válido: {champion_id}")

# ==================== Endpoints ====================
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/summoner")
async def post_summoner(request: SummonerRequest, http_request: Request):
    return await _handle_summoner(request.summoner_name, request.region, http_request)

@app.get("/api/summoner")
async def get_summoner(
    summoner_name: str = Query(..., max_length=100),
    region: str = Query(..., max_length=10),
    http_request: Request = None
):
    return await _handle_summoner(summoner_name, region, http_request)

async def _handle_summoner(summoner_name: str, region: str, http_request: Request):
    ip = get_client_ip(http_request)
    allowed, used = await check_and_increment_search_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Has excedido el límite de búsquedas de invocadores por hoy. Vuelve mañana."
        )

    try:
        if region not in REGION_MAP:
            raise HTTPException(status_code=400, detail="Región no soportada")
        result = await get_summoner_and_mastery(summoner_name, region)

        await save_summoner(
            puuid=result["puuid"],
            game_name=result["name"],
            tag_line=result["tagLine"],
            region=result["region"],
            icon_id=result["iconId"],
            level=result["level"],
        )

        token = create_token(result["puuid"], region)
        result["token"] = token
        return result

    except ValueError:
        logger.warning(f"Invocador no encontrado: {summoner_name} en {region}")
        raise HTTPException(status_code=404, detail="Invocador no encontrado. Verifica el Riot ID y la región.")
    except PermissionError:
        logger.warning("Error de permiso en Riot API")
        raise HTTPException(status_code=403, detail="Error con la clave API. Contacta al soporte.")
    except RuntimeError:
        logger.exception("Error de Riot API")
        raise HTTPException(status_code=429, detail="Demasiadas peticiones a Riot. Espera un momento e intenta de nuevo.")
    except Exception:
        logger.exception("Error interno en /api/summoner")
        raise HTTPException(status_code=500, detail="Error interno del servidor. Intenta más tarde.")

# ==================== /api/chat SIN AUTENTICACIÓN JWT ====================
@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
):
    logger.info(f"Chat request para puuid={request.puuid[:8]}... region={request.region}")

    try:
        champ_id_int, real_name = await _resolve_champion_id(request.championId)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    champion_title = request.championTitle or "Campeón de League of Legends"

    # Rate limit de chat (con caché en memoria)
    ip = get_client_ip(http_request)
    allowed, used = await check_and_increment_chat_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Llegaste al límite de 10 mensajes por día en esta demo. Probá de nuevo mañana.",
        )

    history = await get_chat_history(request.puuid, request.championId)
    player_info = await get_cached_player_info(request.puuid, request.region)

    text = await get_ai_response(
        champion_name=real_name,
        champion_title=champion_title,
        persona=request.persona,
        history=history,
        message=request.message,
        summoner_name=player_info["name"],
        player_context=player_info["context"],
    )

    await append_chat_messages(
        puuid=request.puuid,
        champion_id=request.championId,
        user_message=request.message,
        champion_message=text,
    )

    return {"text": text, "remaining": max(0, 10 - used)}

@app.get("/api/chat/limit")
async def chat_limit(http_request: Request):
    ip = get_client_ip(http_request)
    # Usa la función mejorada que también cachea en memoria
    status = await get_chat_limit_status(ip)
    return status

# ==================== Historial (protegido con JWT) ====================
@app.get("/api/chat/history")
async def chat_history(
    puuid: str = Query(..., max_length=100),
    championId: str = Query(..., max_length=50),
    token_data: Tuple[str, str] = Depends(get_puuid_from_token)
):
    token_puuid, token_region = token_data
    if token_puuid != puuid:
        raise HTTPException(status_code=403, detail="No autorizado para este puuid")
    try:
        await _resolve_champion_id(championId)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await get_chat_history(puuid, championId)

@app.delete("/api/chat/history")
async def delete_chat_history(
    puuid: str = Query(..., max_length=100),
    championId: str = Query(..., max_length=50),
    token_data: Tuple[str, str] = Depends(get_puuid_from_token)
):
    token_puuid, token_region = token_data
    if token_puuid != puuid:
        raise HTTPException(status_code=403, detail="No autorizado para este puuid")
    try:
        await _resolve_champion_id(championId)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await clear_chat_history(puuid, championId)
    return {"success": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)