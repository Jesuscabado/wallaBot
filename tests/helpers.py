"""Utilidades compartidas por los tests (se importan como `from helpers import ...`)."""
import json
import time as _time

import bot_alertas as b

# La función real, guardada antes de que conftest la bloquee para que los tests no usen la red
NUEVA_SESION_REAL = b.nueva_sesion


class Fin(BaseException):
    """Para cortar los bucles infinitos del bot (que capturan Exception, no BaseException)."""


def anuncio(i, titulo=None, precio=10, plataforma="wallapop"):
    return {"id": f"{plataforma}:{i}", "titulo": titulo or f"Anuncio {i}", "precio": precio,
            "url": url(i, plataforma), "foto": None, "plataforma": plataforma.capitalize()}


def url(i, plataforma="wallapop"):
    return f"https://x/{plataforma}/{i}"


def config_con(busquedas, **extra):
    """Configuración completa con solo Wallapop, sin resumen diario y con las búsquedas indicadas."""
    c = json.loads(json.dumps(b.CONFIG_DEFECTO))
    c["plataformas"] = ["wallapop"]
    c["busquedas"] = busquedas
    c["alertas_salud"]["latido_diario"] = False
    c.update(extra)
    return c


def nuevo_estado(**extra):
    e = {"salud": {}, "pausa_hasta": {}, "avisos": 0, "ciclos": 0, "pendientes": [], "silencio_hasta": 0}
    e.update(extra)
    return e


class Reloj:
    """Sustituye al módulo `time` dentro del bot: sleep no espera y se puede fijar la hora."""

    def __init__(self):
        self.t = None            # None = hora real
        self.dormido = []
        self.al_dormir = None

    def time(self):
        return _time.time() if self.t is None else self.t

    def sleep(self, s):
        self.dormido.append(s)
        if self.al_dormir:
            self.al_dormir(s)

    def localtime(self, s=None):
        if s is None and self.t is not None:
            s = self.t
        return _time.localtime(s)

    def strftime(self, formato, t=None):
        return _time.strftime(formato, self.localtime() if t is None else t)

    def __getattr__(self, nombre):   # mktime, etc.: lo demás es el módulo real
        return getattr(_time, nombre)


class Resp:
    def __init__(self, code=200, j=None, texto=None):
        self.status_code, self._j = code, j
        self.text = texto if texto is not None else json.dumps(j if j is not None else {})
        self.ok = 200 <= code < 400

    def json(self):
        if self._j is None:
            raise ValueError("no es JSON")
        return self._j


class Cookies:
    def keys(self):
        return ["access_token_web", "anon_id"]


def _devolver(resp):
    if isinstance(resp, Exception):
        raise resp
    return resp


class Sesion:
    """Sesión HTTP falsa que responde según un fragmento de la URL (el más largo gana).

    La clave especial "HOME" corresponde a la página principal de Vinted.
    """

    def __init__(self, respuestas):
        self.r, self.cookies, self.llamadas = respuestas, Cookies(), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.llamadas.append((url, dict(params or {})))
        if "HOME" in self.r and url == b.VINTED_DOMINIO:
            return _devolver(self.r["HOME"])
        for frag, resp in sorted(self.r.items(), key=lambda kv: -len(kv[0])):
            if frag != "HOME" and frag in url:
                return _devolver(resp)
        raise AssertionError(f"URL no prevista en el test: {url}")


class SesionSeq:
    """Sesión falsa que devuelve las respuestas en orden, sea cual sea la URL."""

    def __init__(self, respuestas):
        self.r, self.cookies, self.llamadas = list(respuestas), Cookies(), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.llamadas.append((url, dict(params or {})))
        return _devolver(self.r.pop(0))
