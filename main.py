import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from ia import get_ai_response
from riot import get_summoner_and_mastery, REGION_MAP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai-chat")

app = FastAPI(title="AI Chat Backend", version="1.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# Modelos de datos
# =========================

class SummonerRequest(BaseModel):
    name: str   # solo nombre, sin tag
    region: str

class SummonerResponse(BaseModel):
    name: str
    region: str
    level: int
    iconId: int
    puuid: str
    topChampions: List[Dict[str, Any]]

# =========================
# Endpoints
# =========================
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/summoner", response_model=SummonerResponse)
async def get_summoner(request: SummonerRequest):
    """
    Busca un invocador por nombre y región (SIN tagline).
    """
    try:
        # Validar región
        if request.region not in REGION_MAP:
            raise HTTPException(status_code=400, detail="Región no soportada")

        result = await get_summoner_and_mastery(
            summoner_name=request.name,
            region_key=request.region
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.exception("Error en /api/summoner")
        raise HTTPException(status_code=500, detail="Error interno del servidor")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)