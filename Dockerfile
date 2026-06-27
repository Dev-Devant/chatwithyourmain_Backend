# Usa una imagen ligera de Python
FROM python:3.11-slim

# Establece el directorio de trabajo
WORKDIR /app

# Copia las dependencias primero (para aprovechar caché)
COPY requirements.txt .

# Instala las dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copia el resto del código
COPY . .

# Expone el puerto (Railway asignará el puerto real mediante $PORT)
EXPOSE 8000

# Comando de inicio - usamos uvicorn con un solo worker para serverless
CMD ["gunicorn", "main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "1", "--timeout", "60", "--bind", "0.0.0.0:8000"]