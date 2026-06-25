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
    allow_credentials=False,  # No usamos cookies ni sesiones
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== Security Headers ====================
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://ddragon.leagueoflegends.com; "
        "connect-src 'self' https://ddragon.leagueoflegends.com; "
        "frame-ancestors 'none';"
    )
    return response

# ==================== JWT ====================
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    logger.warning("JWT_SECRET no configurada, usando valor aleatorio (no recomendado para producción)")
    JWT_SECRET = secrets.token_urlsafe(32)

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_MINUTES = 60 * 24  # 24 horas

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
    summoner_name: str = Field(..., max_length=100, description="Riot ID completo, ej. Nombre#TAG")
    region: str = Field(..., max_length=10)

class ChatRequest(BaseModel):
    championId: str = Field(..., max_length=50)
    championName: Optional[str] = None   # se ignora, se obtiene del mapa
    championTitle: Optional[str] = None  # se ignora o se usa como fallback
    persona: str = Field(..., max_length=500)  # personalidad enviada por el front
    message: str = Field(..., max_length=500)
    puuid: str = Field(..., max_length=100)
    region: str = Field(..., max_length=10)

# ==================== Eventos ====================
@app.on_event("startup")
async def startup():
    await init_db()
    await init_redis()
    # Precargar el mapa de campeones para que esté en caché
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

# ==================== Endpoints ====================
@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/api/summoner")
async def post_summoner(request: SummonerRequest, http_request: Request):
    return await _handle_summoner(request.summoner_name, request.region, http_request)

@app.get("/api/summoner")
async def get_summoner(
    summoner_name: str = Query(..., max_length=100, description="Riot ID completo, ej. Nombre#TAG"),
    region: str = Query(..., max_length=10),
    http_request: Request = None
):
    return await _handle_summoner(summoner_name, region, http_request)

async def _handle_summoner(summoner_name: str, region: str, http_request: Request):
    # Rate limit por IP (búsquedas)
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

        # Generar token JWT para este puuid
        token = create_token(result["puuid"], region)
        result["token"] = token
        return result

    except ValueError:
        logger.warning(f"Error de usuario: invocador no encontrado para {summoner_name} en {region}")
        raise HTTPException(status_code=404, detail="Invocador no encontrado. Verifica el Riot ID y la región.")
    except PermissionError:
        logger.warning(f"Error de permiso en Riot API para {summoner_name}")
        raise HTTPException(status_code=403, detail="Error con la clave API. Contacta al soporte.")
    except RuntimeError:
        logger.exception(f"Error de Riot API para {summoner_name}")
        raise HTTPException(status_code=429, detail="Demasiadas peticiones a Riot. Espera un momento e intenta de nuevo.")
    except Exception:
        logger.exception("Error interno en /api/summoner")
        raise HTTPException(status_code=500, detail="Error interno del servidor. Intenta más tarde.")

@app.post("/api/chat")
async def chat(
    request: ChatRequest,
    http_request: Request,
    token_data: Tuple[str, str] = Depends(get_puuid_from_token)
):
    token_puuid, token_region = token_data
    if token_puuid != request.puuid or token_region != request.region:
        raise HTTPException(status_code=403, detail="No autorizado para este puuid")

    # Validar championId contra el mapa de campeones de Data Dragon
    champion_map = await _get_champion_map()
    # champion_map es Dict[int, str] (id numérico -> nombre)
    try:
        champ_id_int = int(request.championId)
    except ValueError:
        raise HTTPException(status_code=400, detail="championId debe ser un número entero")
    if champ_id_int not in champion_map:
        raise HTTPException(status_code=400, detail="Campeón no soportado o ID inválido")

    # Obtenemos el nombre real del campeón desde el mapa
    real_name = champion_map[champ_id_int]
    # Usamos el título del front si viene, sino uno genérico
    champion_title = request.championTitle or "Campeón de League of Legends"

    # Rate limit de chat
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
    return await get_chat_limit_status(ip)

# ========== HISTORIAL (protegido con token) ==========
@app.get("/api/chat/history")
async def chat_history(
    puuid: str = Query(..., max_length=100),
    championId: str = Query(..., max_length=50),
    token_data: Tuple[str, str] = Depends(get_puuid_from_token)
):
    token_puuid, token_region = token_data
    if token_puuid != puuid:
        raise HTTPException(status_code=403, detail="No autorizado para este puuid")
    # Validar championId (opcional)
    champion_map = await _get_champion_map()
    try:
        if int(championId) not in champion_map:
            raise HTTPException(status_code=400, detail="Campeón inválido")
    except ValueError:
        raise HTTPException(status_code=400, detail="championId debe ser un número")
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
    champion_map = await _get_champion_map()
    try:
        if int(championId) not in champion_map:
            raise HTTPException(status_code=400, detail="Campeón inválido")
    except ValueError:
        raise HTTPException(status_code=400, detail="championId debe ser un número")
    await clear_chat_history(puuid, championId)
    return {"success": True}

# ========== ENDPOINT /api/summoners ELIMINADO ==========
# Se eliminó para no filtrar puuids.

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)