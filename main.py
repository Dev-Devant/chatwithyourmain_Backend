import os
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from riot import get_summoner_and_mastery, REGION_MAP

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

# =========================
# Modelos
# =========================


class SummonerRequest(BaseModel):
    summoner_name: str
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
    summoner_name: str = Query(..., description="Nombre de invocador"),
    region: str = Query(..., description="Región (LAN, NA, EUW, ...)")
):
    return await _handle_summoner(summoner_name, region)

async def _handle_summoner(summoner_name: str, region: str):
    try:
        if region not in REGION_MAP:
            raise HTTPException(status_code=400, detail="Región no soportada")
        result = await get_summoner_and_mastery(summoner_name, region)
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