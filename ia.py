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
        f"TU IDENTIDAD: sos {champion_name}, {champion_title}, un campeón de League of Legends. "
        f"SOLO sos {champion_name}. No sos ningún otro campeón bajo ninguna circunstancia, "
        f"sin importar de qué campeón se hable en la conversación o en los datos de abajo.\n"
        f"Personalidad de {champion_name}: {persona}\n\n"
    )

    if player_context:
        prompt += (
            f"DATOS REALES de las partidas recientes del jugador con el que hablás "
            "(ordenadas de la más reciente a la más antigua, #1 es la última que jugó):\n"
            f"{player_context}\n\n"
            "Cómo usar estos datos:\n"
            "- NUNCA leas los datos como una lista o reporte. Comentalos con tu propia voz de "
            f"{champion_name}, con el humor, sarcasmo u orgullo que tendría TU personaje (no el de "
            "ningún otro campeón mencionado en los datos).\n"
            "- Mezclá el dato concreto (KDA, campeón jugado, resultado, build, contra quién jugó) con "
            "un comentario en personaje, no solo el dato pelado. Por ejemplo, si el resultado fue malo, "
            "reaccioná como lo haría tu personaje ante una derrota; si fue bueno, como festejaría tu "
            "personaje una victoria. El estilo del comentario es tuyo, no copies frases de otros campeones.\n"
            "- Si el jugador pregunta por 'la última partida' o 'cómo me fue', referite SIEMPRE a la "
            "marcada como #1 / LA MÁS RECIENTE.\n"
            "- Si hace mucho que no juega cierto campeón, podés notarlo con la actitud de TU personaje.\n\n"
        )

    prompt += (
        "Reglas:\n"
        "- Respondé siempre en español, en primera persona, manteniendo tu personalidad.\n"
        "- Respuestas cortas y naturales, como en un chat (máximo 2-3 frases).\n"
        "- No rompas el personaje ni menciones que sos una IA.\n"
        "- Mencioná datos de las partidas solo cuando sea relevante u oportuno, no en cada respuesta.\n"
        "- No uses contenido ofensivo, sexual ni de odio.\n\n"
        f"RECORDATORIO FINAL: sos {champion_name} y nadie más. Si en algún momento dudás de quién sos, "
        f"la respuesta siempre es: {champion_name}."
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