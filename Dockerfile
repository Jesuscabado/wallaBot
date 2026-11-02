FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# El script se copia por si ejecutas la imagen suelta; con docker compose
# se monta encima desde tu carpeta, así puedes editarlo sin reconstruir.
COPY bot_alertas.py /app/bot_alertas.py

# vistos.json se guarda en el directorio de trabajo (/data), que es un volumen
WORKDIR /data
CMD ["python", "/app/bot_alertas.py"]
