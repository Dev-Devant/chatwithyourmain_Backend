import os
import logging
from typing import List, Dict, Optional
from fastapi import HTTPException
from openai import AsyncOpenAI

logger = logging.getLogger("ai-chat")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY no configurada")

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

MAX_HISTORY_MESSAGES = 12


def _build_system_prompt(
    champion_name: str,
    champion_title: str,
    persona: str,
    player_context: Optional[str] = None,
    ) -> str:
    prompt = (
        f"Eres {champion_name}, {champion_title}, un campeón de League of Legends.\n"
        f"Personalidad: {persona}\n\n"
    )

    if player_context:
        prompt += (
            "DATOS REALES de las partidas recientes del jugador con el que hablás "
            "(ordenadas de la más reciente a la más antigua, #1 es la última que jugó):\n"
            f"{player_context}\n\n"
            "Cómo usar estos datos:\n"
            "- NUNCA leas los datos como una lista o reporte. Comentalos como lo haría tu personaje, "
            "con humor, sarcasmo, orgullo o lo que corresponda a tu personalidad.\n"
            "- Ejemplo de tono (adaptalo a tu propia personalidad, esto es solo referencia de estilo): "
            "\"Esa partida 0/5/13... no exploté a nadie, pero al menos ganamos. Fue divertido reventar "
            "las torres de Ashe.\"\n"
            "- Mezclá el dato concreto (KDA, campeón, resultado, build, contra quién jugó) con un "
            "comentario en personaje, no solo el dato pelado.\n"
            "- Si el jugador pregunta por 'la última partida' o 'cómo me fue', referite SIEMPRE a la "
            "marcada como #1 / LA MÁS RECIENTE. No mezcles con partidas viejas salvo que el jugador "
            "pregunte específicamente por una racha, un campeón puntual, o pida comparar.\n"
            "- Si hace mucho que no juega cierto campeón, podés hacerle notar eso con la actitud de tu "
            "personaje (reproche, indiferencia, burla, etc. según corresponda).\n\n"
        )

    prompt += (
        "Reglas:\n"
        "- Respondé siempre en español, en primera persona, manteniendo tu personalidad.\n"
        "- Respuestas cortas y naturales, como en un chat (máximo 2-3 frases).\n"
        "- No rompas el personaje ni menciones que sos una IA.\n"
        "- Mencioná datos de las partidas solo cuando sea relevante u oportuno, no en cada respuesta.\n"
        "- No uses contenido ofensivo, sexual ni de odio."
    )
    return prompt


def _build_messages(
    champion_name: str,
    champion_title: str,
    persona: str,
    history: List[Dict[str, str]],
    message: str,
    player_context: Optional[str] = None,
) -> List[Dict[str, str]]:
    messages = [
        {
            "role": "system",
            "content": _build_system_prompt(champion_name, champion_title, persona, player_context),
        }
    ]

    trimmed_history = history[-MAX_HISTORY_MESSAGES:] if history else []
    for entry in trimmed_history:
        role = "assistant" if entry.get("role") == "champion" else "user"
        text = entry.get("text", "")
        if text:
            messages.append({"role": role, "content": text})

    messages.append({"role": "user", "content": message})
    return messages


async def get_ai_response(
    champion_name: str,
    champion_title: str,
    persona: str,
    history: List[Dict[str, str]],
    message: str,
    player_context: Optional[str] = None,
) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY no configurada en el servidor")

    messages = _build_messages(champion_name, champion_title, persona, history, message, player_context)

    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.8,
            max_tokens=300,
        )
        text = completion.choices[0].message.content
        return text.strip() if text else "..."
    except Exception:
        logger.exception("Error llamando a OpenAI")
        raise HTTPException(status_code=500, detail="Error del servicio de IA")