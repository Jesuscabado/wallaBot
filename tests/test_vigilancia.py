"""El vigilante: reinicio si se atasca, health.json y los pings al servicio externo."""
import json

import pytest

import bot_alertas as b
from helpers import Resp, nuevo_estado


def avanzar(reloj, estado, parar_en, paso=30):
    """Cada «sleep» del vigilante hace avanzar el reloj 30 s y el bot «avanza» con normalidad."""
    cuenta = {"n": 0}

    def al_dormir(_):
        cuenta["n"] += 1
        reloj.t += paso
        estado["pulso"] = reloj.t
        if cuenta["n"] >= parar_en:
            estado["_parar"] = True

    reloj.al_dormir = al_dormir


def ejecutar(reloj, parar_en, resultados=None, **kwargs):
    reloj.t = 1000.0
    estado = nuevo_estado(pulso=reloj.t)
    avanzar(reloj, estado, parar_en)
    pings, salidas = [], []
    pendientes = iter(resultados) if resultados else None

    def ping(url):
        pings.append((reloj.t, url))
        return next(pendientes) if pendientes else True

    b.vigilante(estado, limite=600, pausa=0, salir=salidas.append, ping=ping, **kwargs)
    return pings, salidas, estado


# ------------------------------------- pings -------------------------------------

def test_ping_ok_y_ping_fallido(monkeypatch):
    monkeypatch.setattr(b.requests, "get", lambda url, timeout=None: Resp(200))
    assert b.ping_externo("https://hc-ping.com/x") is True
    monkeypatch.setattr(b.requests, "get", lambda url, timeout=None: Resp(500))
    assert b.ping_externo("https://hc-ping.com/x") is False


def test_el_ping_no_filtra_la_url_secreta_en_los_logs(monkeypatch, capsys):
    def falla(url, timeout=None):
        raise b.requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(b.requests, "get", falla)
    assert b.ping_externo("https://hc-ping.com/UUID-SECRETO") is False
    salida = capsys.readouterr().out
    assert "UUID-SECRETO" not in salida and "ConnectionError" in salida


def test_sano_hace_un_ping_al_principio_y_despues_cada_5_minutos(reloj, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/abc")
    pings, salidas, _ = ejecutar(reloj, 25, cada=300)
    assert pings == [(1030.0, "https://hc-ping.com/abc"), (1330.0, "https://hc-ping.com/abc"),
                     (1630.0, "https://hc-ping.com/abc")] and salidas == []


def test_si_el_ping_falla_reintenta_al_minuto(reloj, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/abc")
    pings, _, _ = ejecutar(reloj, 15, resultados=[False, True, True, True], cada=300)
    assert [t for t, _ in pings[:3]] == [1030.0, 1090.0, 1390.0]       # fallo → +60 s; después, cada 300 s


def test_sin_url_no_hay_pings(reloj):
    pings, salidas, _ = ejecutar(reloj, 20, cada=300)
    assert pings == [] and salidas == []


def test_la_cadencia_de_los_pings_se_configura_por_entorno(reloj, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/abc")
    monkeypatch.setenv("HEALTHCHECK_CADA", "60")
    assert [t for t, _ in ejecutar(reloj, 8)[0]] == [1030.0, 1090.0, 1150.0, 1210.0]


def test_una_cadencia_no_valida_usa_300_segundos(reloj, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/abc")
    monkeypatch.setenv("HEALTHCHECK_CADA", "basura")
    assert [t for t, _ in ejecutar(reloj, 12)[0]] == [1030.0, 1330.0]


# ------------------------------------ atascos ------------------------------------

def vigilante_atascado(reloj, telegram, url=None, monkeypatch=None, **kwargs):
    if url and monkeypatch:
        monkeypatch.setenv("HEALTHCHECK_URL", url)
    reloj.t = 1000.0
    estado = nuevo_estado(pulso=reloj.t - 900)                        # sin avances desde hace 15 minutos
    pings, salidas = [], []
    b.vigilante(estado, pausa=0, salir=salidas.append, ping=lambda u: pings.append(u) or True, **kwargs)
    return pings, salidas


def test_si_se_atasca_avisa_manda_fail_y_sale(reloj, telegram, monkeypatch):
    pings, salidas = vigilante_atascado(reloj, telegram, "https://hc-ping.com/abc/", monkeypatch, limite=600)
    assert salidas == [1] and pings == ["https://hc-ping.com/abc/fail"]     # aunque la URL termine en /
    assert any("atascado" in m and "15 min" in m for m in telegram.mensajes)


def test_si_se_atasca_sin_url_solo_avisa_y_sale(reloj, telegram):
    pings, salidas = vigilante_atascado(reloj, telegram, limite=600)
    assert salidas == [1] and pings == [] and telegram.mensajes


def test_esperar_entre_ciclos_no_es_un_atasco(reloj, telegram):
    reloj.t = 1000.0
    estado = nuevo_estado(pulso=reloj.t + 3600)                       # el bucle principal duerme hasta dentro de una hora
    cuenta = {"n": 0}

    def al_dormir(_):
        cuenta["n"] += 1
        if cuenta["n"] >= 3:
            estado["_parar"] = True

    reloj.al_dormir = al_dormir
    salidas = []
    b.vigilante(estado, limite=600, pausa=0, salir=salidas.append)
    assert salidas == [] and telegram.mensajes == []


def test_limite_cero_desactiva_el_reinicio(reloj, telegram):
    reloj.t = 1000.0
    estado = nuevo_estado(pulso=reloj.t - 99999)
    reloj.al_dormir = lambda _: estado.update(_parar=True)
    salidas = []
    b.vigilante(estado, limite=0, pausa=0, salir=salidas.append)
    assert salidas == []


@pytest.mark.parametrize("entorno, sin_avance, sale", [
    ("50", 100, True), ("50", 10, False), (None, 700, True), (None, 500, False), ("basura", 700, True), ("basura", 500, False),
])
def test_el_limite_se_lee_del_entorno_o_vale_600(reloj, telegram, monkeypatch, entorno, sin_avance, sale):
    if entorno:
        monkeypatch.setenv("WATCHDOG_SEGUNDOS", entorno)
    reloj.t = 1000.0
    estado = nuevo_estado(pulso=reloj.t - sin_avance)
    reloj.al_dormir = lambda _: estado.update(_parar=True)
    salidas = []
    b.vigilante(estado, pausa=0, salir=salidas.append)
    assert (salidas == [1]) is sale


def test_escribe_health_json_mientras_esta_sano(reloj, tmp_path):
    ejecutar(reloj, 3)
    assert json.loads((tmp_path / "health.json").read_text())["t"] == pytest.approx(1090.0)
