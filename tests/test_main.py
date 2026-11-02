"""Arranque del programa: credenciales, migración, bucle principal, .env y curl_cffi."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import bot_alertas as b
from helpers import NUEVA_SESION_REAL, Fin, Resp, Sesion, anuncio, url

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def arranque(monkeypatch):
    """Prepara main() para ejecutarse sin hilos ni esperas reales."""
    monkeypatch.setattr(sys, "argv", ["bot_alertas.py"])
    monkeypatch.setattr(b, "escuchar_comandos", lambda estado: None)
    monkeypatch.setattr(b, "vigilante", lambda estado: None)


def cortar_tras(monkeypatch, n, al_esperar=None):
    esperas = []

    def espera(timeout=None):
        esperas.append(timeout)
        if al_esperar:
            al_esperar(len(esperas), timeout)
        if len(esperas) >= n:
            raise Fin

    monkeypatch.setattr(b.DESPERTAR, "wait", espera)
    return esperas


def test_sin_credenciales_termina_con_un_mensaje(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["bot_alertas.py"])
    monkeypatch.setattr(b, "TELEGRAM_TOKEN", "")
    with pytest.raises(SystemExit, match="Faltan TELEGRAM_TOKEN"):
        b.main()


def test_arranca_migra_la_memoria_antigua_y_busca(arranque, monkeypatch, telegram, web, cfg_json, tmp_path):
    (tmp_path / "vistos.json").write_text(json.dumps({"wallapop|zelda": ["wallapop:1"]}))
    cfg_json.escribir({"plataformas": ["wallapop"], "busquedas": [{"keyword": "zelda"}]})
    web.datos = {"zelda": [anuncio(1), anuncio(2)]}
    estados = {}
    ciclo_real = b.ciclo
    monkeypatch.setattr(b, "ciclo", lambda cfg, db, estado: estados.update(e=estado) or ciclo_real(cfg, db, estado))
    esperas = cortar_tras(monkeypatch, 2, lambda n, t: estados["e"].__setitem__("visto", estados["e"]["pulso"]))

    with pytest.raises(Fin):
        b.main()

    assert "iniciado" in telegram.mensajes[0] and "/ayuda" in telegram.mensajes[0]
    avisos = [m for m in telegram.mensajes if "Anuncio" in m]
    assert len(avisos) == 1 and url(2) in avisos[0]                      # el 1 venía del vistos.json: no se repite
    assert (tmp_path / "datos.db").exists() and (tmp_path / "vistos.json.migrado").exists()
    assert len(esperas) == 2 and all(60 <= e <= 207 for e in esperas)    # 180 s ± 15 %
    assert estados["e"]["visto"] > time.time()                           # mientras espera, el pulso apunta al futuro


def test_si_no_hay_config_la_crea(arranque, monkeypatch, telegram, web, tmp_path):
    cortar_tras(monkeypatch, 1)
    with pytest.raises(Fin):
        b.main()
    assert (tmp_path / "config.json").exists()


def test_un_error_inesperado_en_un_ciclo_no_tumba_el_bot(arranque, monkeypatch, telegram, web, cfg_json, capsys):
    cfg_json.escribir({"plataformas": ["wallapop"], "busquedas": [{"keyword": "zelda"}]})
    resultados = iter(["E", "E", "OK", "E", "E", "E", "E"])           # el éxito reinicia la cuenta de errores seguidos

    def ciclo(cfg, db, estado):
        if next(resultados) == "E":
            raise RuntimeError("fallo <raro>")

    monkeypatch.setattr(b, "ciclo", ciclo)
    cortar_tras(monkeypatch, 7)
    with pytest.raises(Fin):
        b.main()
    alertas = [m for m in telegram.mensajes if "error interno" in m]
    assert len(alertas) == 1 and "RuntimeError" in alertas[0] and "&lt;raro&gt;" in alertas[0]    # una sola, tras 3 seguidos
    assert "[ciclo] error inesperado (4 seguidos)" in capsys.readouterr().out


def test_limpia_la_base_de_datos_al_arrancar_y_cada_480_ciclos(arranque, monkeypatch, telegram, web, cfg_json):
    cfg_json.escribir({"plataformas": ["wallapop"], "busquedas": [{"keyword": "zelda"}]})
    llamadas = []
    monkeypatch.setattr(b, "limpiar_db", lambda db: llamadas.append(1))
    monkeypatch.setattr(b, "ciclo", lambda cfg, db, estado: None)
    cortar_tras(monkeypatch, 479)
    with pytest.raises(Fin):
        b.main()
    assert len(llamadas) == 1                                          # solo la del arranque
    llamadas.clear()
    cortar_tras(monkeypatch, 480)
    with pytest.raises(Fin):
        b.main()
    assert len(llamadas) == 2                                          # arranque + al completar el ciclo 480


def test_la_opcion_diagnostico_no_arranca_el_bot(monkeypatch, telegram, cfg_json, capsys):
    cfg_json.escribir({"plataformas": ["wallapop"], "busquedas": [{"keyword": "zelda"}]})
    monkeypatch.setattr(sys, "argv", ["bot_alertas.py", "--diagnostico"])
    sesion = Sesion({"api/v3/search": Resp(403, texto="x"), "general/search": Resp(403, texto="x")})
    monkeypatch.setattr(b, "nueva_sesion", lambda: sesion)
    b.main()
    assert "Wallapop: 🚫" in capsys.readouterr().out and telegram.mensajes == []


# ------------------------------------ .env ------------------------------------

def ejecutar_importando(tmp_path, env_extra=None, contenido_env=None):
    """Importa una copia del script en una carpeta aparte y devuelve las variables de Telegram."""
    app = tmp_path / "app"
    app.mkdir()
    shutil.copy(RAIZ / "bot_alertas.py", app)
    if contenido_env is not None:
        (app / ".env").write_text(contenido_env, encoding="utf-8")
    entorno = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    entorno.update(env_extra or {}, PYTHONPATH=str(app))
    r = subprocess.run([sys.executable, "-c",
                        "import bot_alertas as m, os; print(os.environ.get('TELEGRAM_TOKEN'), os.environ.get('TELEGRAM_CHAT_ID'))"],
                       cwd=app, env=entorno, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


ENV = '# comentario\nTELEGRAM_TOKEN=desde-env\nTELEGRAM_CHAT_ID="42"\nVACIA=\nLINEA_SIN_IGUAL\n'


def test_carga_el_env_junto_al_script_quitando_comillas_y_comentarios(tmp_path):
    assert ejecutar_importando(tmp_path, contenido_env=ENV) == "desde-env 42"


def test_una_variable_de_entorno_real_tiene_prioridad_sobre_el_env(tmp_path):
    assert ejecutar_importando(tmp_path, {"TELEGRAM_TOKEN": "externo"}, ENV) == "externo 42"


def test_sin_env_las_variables_quedan_sin_definir(tmp_path):
    assert ejecutar_importando(tmp_path) == "None None"


# ----------------------------------- curl_cffi -----------------------------------

def test_con_curl_cffi_la_sesion_imita_a_chrome():
    pytest.importorskip("curl_cffi")
    assert b.HAY_CFFI is True
    sesion = NUEVA_SESION_REAL()
    assert type(sesion).__module__.startswith("curl_cffi") and callable(sesion.get)


def test_sin_curl_cffi_usa_requests_con_un_user_agent(monkeypatch):
    monkeypatch.setattr(b, "HAY_CFFI", False)
    sesion = NUEVA_SESION_REAL()
    assert isinstance(sesion, b.requests.Session) and "Mozilla" in sesion.headers["User-Agent"]


def test_avisa_en_el_arranque_si_falta_curl_cffi(arranque, monkeypatch, telegram, web, capsys):
    monkeypatch.setattr(b, "HAY_CFFI", False)
    cortar_tras(monkeypatch, 1)
    with pytest.raises(Fin):
        b.main()
    assert "curl_cffi no está instalado" in capsys.readouterr().out
