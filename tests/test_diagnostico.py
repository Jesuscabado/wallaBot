import json
import threading

import pytest

import bot_alertas as b
from helpers import Resp, Sesion, config_con

CFG = config_con([{"keyword": "game cube"}], plataformas=["wallapop", "vinted"])
W_OK = Resp(200, {"data": {"section": {"payload": {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}}}})
V_OK = Resp(200, {"items": [{"id": 9, "title": "x", "price": {"amount": "5"}}]})
HOME = Resp(200, texto="<html>home</html>")


def usar(monkeypatch, wall, vint=None):
    """La primera sesión que se pida es la de Wallapop y la segunda, la de Vinted."""
    sesiones = iter([Sesion(wall)] + ([Sesion(vint)] if vint is not None else []))
    monkeypatch.setattr(b, "nueva_sesion", lambda: next(sesiones))


def vinted_ok():
    return {"HOME": HOME, "svc-catalogue": V_OK, "/api/v2/catalog": Resp(404, texto="x")}


def wallapop(**respuestas):
    return {"api/v3/search": respuestas.get("nuevo", Resp(403, texto="x")),
            "general/search": respuestas.get("viejo", Resp(403, texto="x"))}


@pytest.mark.parametrize("res, esperado", [
    ([(200, 3), (403, None)], "✅ funciona (3 resultados)"),
    ([(200, 0), (403, None)], "⚠️ responde pero sin resultados"),
    ([(403, None), (404, None)], "🚫"),                      # 403 pesa más que 404
    ([(429, None), (404, None)], "⏳"),
    ([(404, None), (404, None)], "❓"),
    ([(None, None), (None, None)], "🔌"),
    ([(500, None), (502, None)], "❌"),
])
def test_veredictos(res, esperado):
    assert b._veredicto(res).startswith(esperado)


def test_informe_cuando_todo_funciona(monkeypatch):
    usar(monkeypatch, wallapop(nuevo=W_OK), vinted_ok())
    inf = b.informe_diagnostico(CFG)
    assert inf["veredictos"] == {"Wallapop": "✅ funciona (3 resultados)", "Vinted": "✅ funciona (1 resultados)"}
    detalle = "\n".join(inf["detalle"])
    assert "anuncios: 3" in detalle and "campos de un anuncio" in detalle and "access_token_web" in detalle


@pytest.mark.parametrize("wall, esperado", [
    (wallapop(), "🚫"),
    (wallapop(nuevo=Resp(404, texto="x"), viejo=Resp(404, texto="x")), "❓"),
    (wallapop(nuevo=Resp(200, {"data": {}})), "⚠️"),
    (wallapop(nuevo=Resp(200, texto="<html>")), "⚠️"),                 # 200 pero no es JSON
    (wallapop(nuevo=ConnectionError("sin red"), viejo=TimeoutError("lento")), "🔌"),
])
def test_informe_de_wallapop_segun_la_respuesta(monkeypatch, wall, esperado):
    usar(monkeypatch, wall, vinted_ok())
    assert b.informe_diagnostico(CFG)["veredictos"]["Wallapop"].startswith(esperado)


def test_informe_de_vinted_con_la_home_caida(monkeypatch):
    usar(monkeypatch, wallapop(nuevo=W_OK), {"HOME": ConnectionError("dns")})
    assert b.informe_diagnostico(CFG)["veredictos"]["Vinted"].startswith("🔌")


def test_solo_prueba_las_plataformas_configuradas(monkeypatch):
    usar(monkeypatch, wallapop(nuevo=W_OK))
    assert list(b.informe_diagnostico({**CFG, "plataformas": ["wallapop"]})["veredictos"]) == ["Wallapop"]


def test_sin_busquedas_usa_una_palabra_de_prueba(monkeypatch):
    usar(monkeypatch, wallapop(nuevo=W_OK), vinted_ok())
    assert "Wallapop" in b.informe_diagnostico({**CFG, "busquedas": []})["veredictos"]


# -------------------------------- comando de Telegram --------------------------------

HOSTIL = "<script>alert('x')</script>&" * 60


def hostil(monkeypatch):
    usar(monkeypatch,
         wallapop(nuevo=Resp(403, texto=HOSTIL), viejo=Resp(403, texto=HOSTIL)),
         {"HOME": Resp(200, texto="<html>"), "svc-catalogue": Resp(404, texto=HOSTIL), "/api/v2/catalog": Resp(404, texto=HOSTIL)})


def test_el_comando_responde_con_veredictos_y_detalle(cmd, cfg_json, telegram, monkeypatch):
    cfg_json.escribir(CFG)
    hostil(monkeypatch)
    r = cmd("/diagnostico")
    assert any("Ejecutando diagnóstico" in m for m in telegram.mensajes)          # aviso previo
    assert "🚫" in r and "❓" in r and "curl_cffi" in r and r.count("<pre>") == 1 and r.count("</pre>") == 1


def test_el_mensaje_cabe_en_telegram_y_esta_escapado(cmd, cfg_json, monkeypatch):
    cfg_json.escribir(CFG)
    hostil(monkeypatch)
    r = cmd("/diagnostico")
    assert len(r) < 4096 and "<script>" not in r and "&lt;script&gt;" in r


def test_no_filtra_el_token(cmd, cfg_json, telegram, monkeypatch):
    cfg_json.escribir(CFG)
    hostil(monkeypatch)
    assert "TOKEN-DE-PRUEBA" not in cmd("/diagnostico") + telegram.todo


@pytest.mark.parametrize("hostil_", ["<" * 5000, "&" * 5000, "'\"" * 3000, "normal"])
def test_recortar_html_nunca_parte_una_entidad(hostil_):
    escapado = b._recortar_html(hostil_, 3000)
    assert len(escapado) <= 3000
    for entidad in ("&lt;", "&amp;", "&#x27;", "&quot;", "&gt;"):
        escapado = escapado.replace(entidad, "")
    assert "&" not in escapado


def test_el_diagnostico_no_retiene_el_candado_del_bucle_principal(cmd, monkeypatch):
    libre = {}

    def informe_espia(cfg, n=150):
        def intento():
            libre["ok"] = b.LOCK.acquire(blocking=False)
            if libre["ok"]:
                b.LOCK.release()

        hilo = threading.Thread(target=intento)
        hilo.start()
        hilo.join()
        return {"veredictos": {"Wallapop": "✅"}, "detalle": ["x"], "cffi": True}

    monkeypatch.setattr(b, "informe_diagnostico", informe_espia)
    cmd("/diagnostico")
    assert libre["ok"] is True


def test_solo_un_diagnostico_a_la_vez_y_el_candado_se_libera(cmd, monkeypatch):
    b._diag_lock.acquire()
    try:
        assert "Ya hay un diagnóstico" in cmd("/diagnostico")
    finally:
        b._diag_lock.release()

    def roto(cfg, n=150):
        raise RuntimeError("boom <x>")

    monkeypatch.setattr(b, "informe_diagnostico", roto)
    r = cmd("/diagnostico")
    assert "Error ejecutando" in r and "&lt;x&gt;" in r
    assert b._diag_lock.acquire(blocking=False)                                     # libre incluso tras fallar
    b._diag_lock.release()


def test_version_de_terminal(cfg_json, monkeypatch, capsys):
    cfg_json.escribir(CFG)
    usar(monkeypatch, wallapop(nuevo=W_OK), {"HOME": HOME, "svc-catalogue": Resp(404, texto="x"), "/api/v2/catalog": Resp(404, texto="x")})
    b.diagnostico()
    salida = capsys.readouterr().out
    assert "Wallapop: ✅" in salida and "Vinted: ❓" in salida and "== WALLAPOP ==" in salida


def test_la_alerta_de_salud_remite_al_diagnostico(telegram, estado):
    for _ in range(3):
        b.evaluar_salud("vinted", 0, 2, "HTTP 404", config_con([]), estado)
    assert "/diagnostico" in telegram.mensajes[0]
