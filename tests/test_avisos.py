import json
import time

import pytest

import bot_alertas as b
from helpers import anuncio, config_con, nuevo_estado, url

CH = "🔥 <b>Posible chollo</b>: 50% por debajo"
BJ = "📉 <b>Bajada de precio</b>: 20 € → 10 €"
CFG = config_con([])
BUS = {"keyword": "zelda"}


# ------------------------------------ formato ------------------------------------

def test_formatear_incluye_los_datos_y_escapa_el_html():
    texto = b.formatear(anuncio(1, "A <b>&", 12.5), "k<1>", "NOTA")
    assert texto == ("🔔 <b>Wallapop</b> · «k&lt;1&gt;»\nA &lt;b&gt;&amp;\n💶 12.5 €\nNOTA\nhttps://x/wallapop/1")


def test_formatear_sin_precio_ni_nota():
    texto = b.formatear(anuncio(1, precio=None), "k")
    assert "💶 s/p" in texto and "NOTA" not in texto


# --------------------------- límite de avisos por búsqueda ---------------------------

@pytest.mark.parametrize("cfg_val, busqueda, esperado", [
    (8, {}, 8), (8, {"max_avisos": 3}, 3), (8, {"max_avisos": 0}, 0), (8, {"max_avisos": 999}, 50),
    (8, {"max_avisos": -4}, 0), (8, {"max_avisos": "x"}, 8),
])
def test_limite_de_avisos(cfg_val, busqueda, esperado):
    cfg = config_con([])
    cfg["max_avisos_por_busqueda"] = cfg_val
    assert b.limite_avisos(cfg, busqueda) == esperado


def test_por_debajo_del_limite_se_envia_todo_del_mas_antiguo_al_mas_nuevo(telegram, estado):
    b.despachar_avisos(CFG, estado, BUS, "zelda", [(anuncio(i), None) for i in (5, 4, 3)])   # llegan del más nuevo
    assert [url(i) in m for i, m in zip((3, 4, 5), telegram.mensajes)] == [True] * 3
    assert estado["avisos"] == 3 and not any("anuncios más" in m for m in telegram.mensajes)


def test_lo_mejor_va_suelto_y_el_resto_agrupado(telegram, estado):
    avisar = [(anuncio(i), None) for i in range(100, 88, -1)]                 # 12 normales, el 100 es el más nuevo
    avisar += [(anuncio(200, "Chollo antiguo", 5), CH), (anuncio(201, "Bajada antigua", 10), BJ)]
    b.despachar_avisos(CFG, estado, {"keyword": "zelda", "max_avisos": 5}, "zelda", avisar)

    resumen, sueltos = telegram.mensajes[0], telegram.mensajes[1:]
    assert len(telegram.mensajes) == 6 and "+9 anuncios más" in resumen and "límite: 5" in resumen
    todos = " ".join(sueltos)
    assert url(200) in todos and url(201) in todos                            # chollo y bajada entran aunque sean antiguos
    assert all(url(i) in todos for i in (100, 99, 98))                         # y luego los más recientes
    assert url(97) not in todos and url(97) in resumen                         # el resto, en el resumen
    assert "🔥" in sueltos[-1]                                                 # lo mejor queda al final del chat
    assert estado["avisos"] == 14 and all(m.count("https://x/") == 1 for m in sueltos)


def test_sin_limite_se_envia_todo_suelto(telegram, estado):
    b.despachar_avisos(CFG, estado, {"keyword": "zelda", "max_avisos": 0}, "zelda", [(anuncio(i), None) for i in range(30)])
    assert len(telegram.mensajes) == 30 and not any("anuncios más" in m for m in telegram.mensajes)


def test_justo_en_el_limite_no_hay_resumen(telegram, estado):
    b.despachar_avisos(CFG, estado, {"keyword": "zelda", "max_avisos": 4}, "zelda", [(anuncio(i), None) for i in range(4)])
    assert len(telegram.mensajes) == 4 and not any("anuncios más" in m for m in telegram.mensajes)


def test_el_resumen_cabe_en_telegram_y_esta_escapado(telegram, estado):
    muchos = [(anuncio(i, "<b>" + "T" * 300 + "</b> & co", 9.5), None) for i in range(120)]
    b.despachar_avisos(CFG, estado, {"keyword": "a<b>", "max_avisos": 2}, "a<b>", muchos)
    resumen = telegram.mensajes[0]
    assert len(resumen) < 4096 and "&lt;b&gt;" in resumen and "a&lt;b&gt;" in resumen and "<b>TTT" not in resumen
    assert "+118 anuncios más" in resumen and "… y " in resumen and "/limite" in resumen
    assert resumen.count("https://x/") <= 25


def test_si_el_resumen_no_se_puede_enviar_no_se_cuenta(telegram, estado):
    telegram.ok = False
    b.despachar_avisos(CFG, estado, {"keyword": "zelda", "max_avisos": 1}, "zelda", [(anuncio(i), None) for i in range(3)])
    assert estado["avisos"] == 0


def test_durante_el_silencio_todo_va_a_pendientes_sin_limite_y_se_guarda(telegram):
    estado = nuevo_estado(silencio_hasta=b.INDEFINIDO)
    avisar = [(anuncio(i), CH if i == 3 else None) for i in range(6)]
    b.despachar_avisos(CFG, estado, {"keyword": "zelda", "max_avisos": 2}, "zelda", avisar)
    assert telegram.mensajes == [] and len(estado["pendientes"]) == 6
    assert estado["pendientes"][0]["url"] == url(5)                                # del más antiguo al más nuevo
    assert any(p["nota"] == CH for p in estado["pendientes"])
    assert len(json.loads(open("estado.json").read())["pendientes"]) == 6


def test_sin_avisos_no_hace_nada(telegram, estado):
    b.despachar_avisos(CFG, estado, BUS, "zelda", [])
    assert telegram.mensajes == []


def test_los_pendientes_tienen_un_tope(estado):
    estado["silencio_hasta"] = b.INDEFINIDO
    b.despachar_avisos(CFG, estado, BUS, "zelda", [(anuncio(i), None) for i in range(b.MAX_PENDIENTES + 50)])
    assert len(estado["pendientes"]) == b.MAX_PENDIENTES


# ------------------------------ resumen de pendientes ------------------------------

def pendiente(i, **extra):
    return {"plataforma": "Wallapop", "titulo": f"Anuncio {i}", "precio": 5, "url": url(i), "kw": "k", "nota": None, **extra}


def test_el_resumen_de_pendientes_se_manda_una_vez_y_se_vacia(telegram, estado):
    estado["pendientes"] = [pendiente(1, titulo="A <b>", nota=CH), pendiente(2, precio=None)]
    b.enviar_pendientes_si_toca(CFG, estado)
    assert len(telegram.mensajes) == 1
    m = telegram.mensajes[0]
    assert "2 anuncios" in m and url(1) in m and url(2) in m and "A &lt;b&gt;" in m and CH in m and "s/p" in m
    assert estado["pendientes"] == [] and estado["avisos"] == 2
    assert json.loads(open("estado.json").read())["pendientes"] == []
    b.enviar_pendientes_si_toca(CFG, estado)
    assert len(telegram.mensajes) == 1                                             # no se repite


def test_un_solo_pendiente_va_en_singular(telegram, estado):
    estado["pendientes"] = [pendiente(1)]
    b.enviar_pendientes_si_toca(CFG, estado)
    assert "1 anuncio mientras" in telegram.mensajes[0]


def test_no_envia_pendientes_mientras_siga_el_silencio(telegram, estado):
    estado.update(pendientes=[pendiente(1)], silencio_hasta=b.INDEFINIDO)
    b.enviar_pendientes_si_toca(CFG, estado)
    assert telegram.mensajes == [] and len(estado["pendientes"]) == 1


def test_un_resumen_largo_se_parte_en_mensajes_y_se_limita_a_40(telegram, estado):
    estado["pendientes"] = [pendiente(i, titulo="T" * 150) for i in range(60)]
    b.enviar_pendientes_si_toca(CFG, estado)
    assert len(telegram.mensajes) >= 2 and all(len(m) <= 4096 for m in telegram.mensajes)
    assert "y 20 más" in telegram.mensajes[-1]


# --------------------------------- silencio ---------------------------------

def hora(h, minuto=30):
    return time.mktime((2026, 10, 27, h, minuto, 0, 0, 0, -1))


@pytest.mark.parametrize("desde, hasta, h, silenciado", [
    (1, 8, 3, True), (1, 8, 1, True), (1, 8, 10, False), (1, 8, 8, False), (1, 8, 0, False),
    (22, 6, 23, True), (22, 6, 2, True), (22, 6, 22, True), (22, 6, 12, False), (22, 6, 6, False),
    (5, 5, 5, False),                                    # mismo valor: no se silencia nada
])
def test_horario_silencioso(estado, desde, hasta, h, silenciado):
    cfg = config_con([], horario_silencioso={"activo": True, "desde": desde, "hasta": hasta})
    assert b.silenciado(cfg, estado, hora(h)) is silenciado


def test_horario_silencioso_inactivo(estado):
    assert not b.silenciado(config_con([]), estado, hora(3))


def test_la_pausa_manual_silencia_hasta_que_vence(estado):
    estado["silencio_hasta"] = hora(12)
    assert b.silenciado(CFG, estado, hora(11)) and not b.silenciado(CFG, estado, hora(13))


# ---------------------------- estado persistente ----------------------------

def test_el_estado_persistente_se_guarda_y_se_lee(estado):
    estado.update(silencio_hasta=123.5, pendientes=[pendiente(1)])
    b.guardar_estado_persistente(estado)
    assert b.cargar_estado_persistente() == {"silencio_hasta": 123.5, "pendientes": [pendiente(1)]}


@pytest.mark.parametrize("contenido", ["basura", "[]", '{"silencio_hasta": "x"}'])
def test_un_estado_ilegible_vuelve_a_los_valores_por_defecto(contenido):
    open("estado.json", "w").write(contenido)
    assert b.cargar_estado_persistente() == {"silencio_hasta": 0.0, "pendientes": []}


def test_sin_archivo_de_estado_se_empieza_de_cero():
    assert b.cargar_estado_persistente() == {"silencio_hasta": 0.0, "pendientes": []}
