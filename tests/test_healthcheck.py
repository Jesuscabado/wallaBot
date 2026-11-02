"""healthcheck.py: lo que ejecuta Docker para saber si el bot está vivo."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "healthcheck.py"


def ejecutar(archivo):
    return subprocess.run([sys.executable, str(SCRIPT)], env={**os.environ, "HEALTH_FILE": str(archivo)},
                          capture_output=True, text=True)


def test_sano_si_hay_una_señal_reciente(tmp_path):
    (tmp_path / "h.json").write_text(json.dumps({"t": time.time() - 30}))
    r = ejecutar(tmp_path / "h.json")
    assert r.returncode == 0 and "ok" in r.stdout


def test_no_sano_si_la_señal_es_antigua(tmp_path):
    (tmp_path / "h.json").write_text(json.dumps({"t": time.time() - 500}))
    r = ejecutar(tmp_path / "h.json")
    assert r.returncode == 1 and "sin señal de vida" in r.stdout


@pytest.mark.parametrize("contenido", ["basura", "[]", '{"otra": 1}', ""])
def test_no_sano_si_el_archivo_es_ilegible(tmp_path, contenido):
    (tmp_path / "h.json").write_text(contenido)
    assert ejecutar(tmp_path / "h.json").returncode == 1


def test_no_sano_si_no_existe_el_archivo(tmp_path):
    r = ejecutar(tmp_path / "no_existe.json")
    assert r.returncode == 1 and "sin datos de salud" in r.stdout
