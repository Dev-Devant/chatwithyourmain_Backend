from openai import AsyncOpenAI
# Inicializar cliente OpenAI (necesitas variable de entorno OPENAI_API_KEY)
client = AsyncOpenAI(api_key=os.getenv("OPENAIAPIKEY"))
# =========================
# FUNCIONES UTILITARIAS
# =========================
async def get_ai_response(user_message: str) -> str:
    """
    Envía el mensaje del usuario al modelo de IA y devuelve la respuesta.
    Puedes personalizar el sistema prompt aquí.
    """
    system_prompt = ( soul.Identidad + soul.TratoUsuario + soul.Estilo + soul.Filosofia + soul.TomaDeDeciciones + soul.Humor + soul.Reglas )
    try:
        completion = await client.chat.completions.create(
            model="gpt-4o-mini",  # o "gpt-3.5-turbo" si prefieres
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=0.7,
            max_tokens=500,
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logger.exception("Error llamando a OpenAI")
        raise HTTPException(status_code=500, detail="Error del servicio de IA")