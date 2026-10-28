FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bot_alertas.py /app/bot_alertas.py

WORKDIR /data

CMD ["python", "/app/bot_alertas.py"]