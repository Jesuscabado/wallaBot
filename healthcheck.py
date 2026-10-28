#!/usr/bin/env python3
"""Healthcheck de Docker: el bot escribe health.json cada ~30 s mientras está vivo."""
import json
import os
import sys
import time
from pathlib import Path

archivo = Path(os.environ.get("HEALTH_FILE", "/data/health.json"))
MAX_EDAD = 180   # segundos sin señal de vida antes de marcarlo como "unhealthy"

try:
    t = json.loads(archivo.read_text())["t"]
except Exception as e:
    print(f"sin datos de salud: {e}")
    sys.exit(1)

edad = time.time() - t
if edad > MAX_EDAD:
    print(f"sin señal de vida desde hace {int(edad)} s")
    sys.exit(1)
print("ok")
sys.exit(0)
