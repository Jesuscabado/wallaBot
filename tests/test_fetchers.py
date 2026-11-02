"""Lectura de respuestas de Wallapop y Vinted con sesiones simuladas (sin red)."""
import pytest

import bot_alertas as b
from helpers import Resp, Sesion, SesionSeq

CFG = {"ubicacion": {"latitud": 40.0, "longitud": -3.0}}


@pytest.mark.parametrize("entrada, esperado", [
    (12, 12.0), ("12,5", 12.5), ("3.5", 3.5), ({"amount": "3"}, 3.0), ({"amount": 7.25}, 7.25),
    (None, None), ("barato", None), ({}, None),
])
def test_a_float(entrada, esperado):
    assert b.a_float(entrada) == esperado


# ----------------------------- Wallapop -----------------------------

def w_item(i):
    return {"id": f"id{i}", "title": f"Zelda {i}", "price": {"amount": 12.5}, "web_slug": f"zelda-{i}",
            "images": [{"urls": {"medium": f"https://img/{i}.jpg"}}]}


def w_pagina(items, siguiente=None):
    return Resp(200, {"data": {"section": {"payload": {"items": items}}}, "meta": {"next_page": siguiente}})


def test_wallapop_lee_anuncios_y_devuelve_el_cursor(monkeypatch):
    sesion = SesionSeq([w_pagina([w_item(1), w_item(2)], "TOKEN2")])
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    items, siguiente = b.buscar_wallapop("zelda", None, CFG)
    assert siguiente == "TOKEN2"
    assert items[0] == {"id": "wallapop:id1", "titulo": "Zelda 1", "precio": 12.5, "foto": "https://img/1.jpg",
                        "url": "https://es.wallapop.com/item/zelda-1", "plataforma": "Wallapop"}
    params = sesion.llamadas[0][1]
    assert params["keywords"] == "zelda" and params["order_by"] == "newest" and "next_page" not in params
    assert params["latitude"] == 40.0 and params["longitude"] == -3.0


def test_wallapop_pide_la_pagina_siguiente_con_el_cursor(monkeypatch):
    sesion = SesionSeq([w_pagina([w_item(3)])])
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    items, siguiente = b.buscar_wallapop("zelda", None, CFG, "TOKEN2")
    assert sesion.llamadas[0][1]["next_page"] == "TOKEN2" and siguiente is None and len(items) == 1


def test_wallapop_usa_el_endpoint_de_respaldo(monkeypatch):
    sesion = Sesion({"api/v3/search": Resp(403, texto="<HTML>"), "general/search": w_pagina([w_item(1)])})
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    items, _ = b.buscar_wallapop("zelda", None, CFG)
    assert len(items) == 1 and len(sesion.llamadas) == 2


def test_wallapop_acepta_el_formato_antiguo_de_la_respuesta(monkeypatch):
    sesion = Sesion({"api/v3/search": Resp(200, {"data": {"search_objects": [w_item(1)]}}), "general/search": Resp(403)})
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    assert len(b.buscar_wallapop("zelda", None, CFG)[0]) == 1


def test_wallapop_anuncio_sin_imagenes_ni_precio(monkeypatch):
    item = {"id": "z", "title": "Sin nada", "web_slug": "sin-nada", "images": []}
    monkeypatch.setattr(b, "_sesion_wallapop", Sesion({"api/v3/search": w_pagina([item]), "general/search": Resp(403)}))
    (anuncio,), _ = b.buscar_wallapop("zelda", None, CFG)
    assert anuncio["precio"] is None and anuncio["foto"] is None


def test_wallapop_si_fallan_todos_los_endpoints_lanza_error_con_los_codigos(monkeypatch):
    sesion = Sesion({"api/v3/search": Resp(403, texto="<HTML>CloudFront"), "general/search": Resp(404, texto="nada")})
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    with pytest.raises(RuntimeError) as e:
        b.buscar_wallapop("zelda", None, CFG)
    assert "HTTP 403" in str(e.value) and "HTTP 404" in str(e.value)


def test_wallapop_respuesta_200_que_no_es_json(monkeypatch):
    sesion = Sesion({"api/v3/search": Resp(200, texto="<html>"), "general/search": Resp(200, texto="<html>")})
    monkeypatch.setattr(b, "_sesion_wallapop", sesion)
    with pytest.raises(RuntimeError, match="no JSON"):
        b.buscar_wallapop("zelda", None, CFG)


# ------------------------------ Vinted ------------------------------

def v_item(i, **extra):
    return {"id": i, "title": f"Zelda {i}", "price": {"amount": "9.0"}, "url": f"https://v/{i}", **extra}


def vinted(monkeypatch, *sesiones):
    pendientes = iter(sesiones)
    monkeypatch.setattr(b, "nueva_sesion", lambda: next(pendientes))


def test_vinted_lee_anuncios_y_pagina_por_numero(monkeypatch):
    sesion = Sesion({"HOME": Resp(200, texto="<html>"), "svc-catalogue": Resp(200, {"items": [v_item(1), v_item(2)]})})
    vinted(monkeypatch, sesion)
    items, siguiente = b.buscar_vinted("zelda", None, CFG)
    assert siguiente == 2 and items[0] == {"id": "vinted:1", "titulo": "Zelda 1", "precio": 9.0, "foto": None,
                                           "url": "https://v/1", "plataforma": "Vinted"}
    assert sesion.llamadas[-1][1]["page"] == 1
    _, siguiente = b.buscar_vinted("zelda", None, CFG, 2)
    assert sesion.llamadas[-1][1]["page"] == 2 and siguiente == 3


def test_vinted_pagina_vacia_termina_la_paginacion(monkeypatch):
    vinted(monkeypatch, Sesion({"HOME": Resp(200), "svc-catalogue": Resp(200, {"items": []})}))
    assert b.buscar_vinted("zelda", None, CFG, 5) == ([], None)


def test_vinted_envia_el_precio_maximo_y_los_parametros_de_sesion():
    p = b._params_vinted("zelda", 30, 3)
    assert p["price_to"] == 30 and p["page"] == 3 and p["search_text"] == "zelda" and p["currency"] == "EUR"
    assert "price_to" not in b._params_vinted("zelda", None)
    assert b._params_vinted("a", None)["global_search_session_id"] != b._params_vinted("a", None)["global_search_session_id"]


def test_vinted_urls_relativas_fotos_y_formato_alternativo(monkeypatch):
    items = [v_item(1, url=None, path="/items/1-zelda", photo={"url": "https://img/1.jpg"}), {"id": 2, "title": "Sin precio"}]
    vinted(monkeypatch, Sesion({"HOME": Resp(200), "svc-catalogue": Resp(200, {"data": {"items": items}})}))
    (uno, dos), _ = b.buscar_vinted("zelda", None, CFG)
    assert uno["url"] == "https://www.vinted.es/items/1-zelda" and uno["foto"] == "https://img/1.jpg"
    assert dos["precio"] is None and dos["url"] == "https://www.vinted.es/items/2"


def test_vinted_renueva_la_sesion_cuando_falla_y_reintenta(monkeypatch):
    caducada = Sesion({"HOME": Resp(200), "svc-catalogue": Resp(404, texto="x"), "/api/v2/catalog": Resp(404, texto="x")})
    nueva = Sesion({"HOME": Resp(200), "svc-catalogue": Resp(200, {"items": [v_item(1)]})})
    vinted(monkeypatch, caducada, nueva)
    items, _ = b.buscar_vinted("zelda", None, CFG)
    assert len(items) == 1 and b._sesion_vinted is nueva            # usó una sesión nueva


def test_vinted_si_siguen_fallando_lanza_error_y_descarta_la_sesion(monkeypatch):
    mala = lambda: Sesion({"HOME": Resp(200), "svc-catalogue": Resp(403, texto="x"), "/api/v2/catalog": Resp(404, texto="y")})
    vinted(monkeypatch, mala(), mala())
    with pytest.raises(RuntimeError) as e:
        b.buscar_vinted("zelda", None, CFG)
    assert "HTTP 403" in str(e.value) and "HTTP 404" in str(e.value) and b._sesion_vinted is None
