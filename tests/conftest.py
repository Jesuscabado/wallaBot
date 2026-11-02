import json
import pathlib

import pytest

import bot_alertas as b
from helpers import Reloj, nuevo_estado


class Telegram:
    """Recoge lo que el bot enviaría a Telegram."""

    def __init__(self):
        self.mensajes, self.fotos, self.ok = [], [], True

    def __call__(self, texto, foto=None, preview=True):
        self.mensajes.append(texto)
        self.fotos.append(foto)
        return self.ok

    def limpiar(self):
        self.mensajes.clear()
        self.fotos.clear()

    @property
    def todo(self):
        return "\n".join(self.mensajes)


class Web:
    """Buscador falso: datos[consulta] = lista de anuncios o una excepción a lanzar."""

    def __init__(self):
        self.datos, self.consultas = {}, []

    def __call__(self, q, max_precio, cfg, cursor=None):
        self.consultas.append(q)
        v = self.datos.get(q, [])
        if isinstance(v, Exception):
            raise v
        return list(v), None


class ConfigJson:
    """Acceso al config.json del directorio de trabajo (temporal en cada test)."""

    ruta = pathlib.Path("config.json")

    def escribir(self, datos):
        self.ruta.write_text(datos if isinstance(datos, str) else json.dumps(datos), encoding="utf-8")

    def leer(self):
        return json.loads(self.ruta.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def reloj(monkeypatch):
    r = Reloj()
    monkeypatch.setattr(b, "time", r)
    return r


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    """Cada test trabaja en una carpeta vacía, con credenciales falsas y SIN acceso a la red."""
    monkeypatch.chdir(tmp_path)
    for var in ("HEALTHCHECK_URL", "HEALTHCHECK_CADA", "WATCHDOG_SEGUNDOS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(b, "TELEGRAM_TOKEN", "TOKEN-DE-PRUEBA")
    monkeypatch.setattr(b, "TELEGRAM_CHAT_ID", "111")
    monkeypatch.setattr(b, "_sesion_wallapop", None)
    monkeypatch.setattr(b, "_sesion_vinted", None)
    monkeypatch.setattr(b, "BUSCADORES", {})

    def sin_red(*args, **kwargs):
        raise AssertionError("Los tests no deben usar la red: simula la respuesta")

    monkeypatch.setattr(b.requests, "get", sin_red)
    monkeypatch.setattr(b.requests, "post", sin_red)
    monkeypatch.setattr(b, "nueva_sesion", sin_red)
    b.DESPERTAR.clear()


@pytest.fixture
def telegram(monkeypatch):
    t = Telegram()
    monkeypatch.setattr(b, "enviar_telegram", t)
    return t


@pytest.fixture
def web(monkeypatch):
    w = Web()
    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": w})
    return w


@pytest.fixture
def db():
    conexion = b.abrir_db()
    yield conexion
    conexion.close()


@pytest.fixture
def estado():
    return nuevo_estado()


@pytest.fixture
def cfg_json():
    return ConfigJson()


@pytest.fixture
def cmd(estado, telegram):
    """Ejecuta un comando de Telegram y devuelve la respuesta."""
    return lambda texto: b.ejecutar_comando(texto, estado)
