import pytest

import bot_alertas as b
from helpers import anuncio, config_con

BUS = {"keyword": "zelda", "max_precio": None}
PAGINAS = {None: ([anuncio(1), anuncio(2)], "c2"), "c2": ([anuncio(3), anuncio(4)], "c3"),
           "c3": ([anuncio(5)], "c4"), "c4": ([anuncio(6)], None)}


@pytest.fixture
def pedidas(monkeypatch):
    llamadas = []

    def buscador(q, mx, cfg, cursor=None):
        llamadas.append(cursor)
        if cursor == "BOOM":
            raise RuntimeError("HTTP 403")
        return PAGINAS[cursor]

    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": buscador})
    return llamadas


def test_primera_pasada_solo_descarga_una_pagina(db, pedidas):
    items, n = b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, True)
    assert n == 1 and len(items) == 2


def test_si_todo_es_desconocido_sigue_hasta_el_maximo(db, pedidas):
    items, n = b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False)
    assert n == 3 and pedidas == [None, "c2", "c3"]
    assert [i["id"] for i in items] == [f"wallapop:{k}" for k in range(1, 6)]


def test_si_la_primera_pagina_ya_tiene_anuncios_conocidos_no_pide_mas(db, pedidas):
    db.execute("INSERT INTO anuncios (id, busqueda, plataforma, ultima) VALUES ('wallapop:2', 'zelda', 'wallapop', 1)")
    db.commit()
    _, n = b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False)
    assert n == 1 and pedidas == [None]


@pytest.mark.parametrize("paginas_maximas, esperado", [(1, 1), (2, 2), (99, 4), (0, 1), ("x", 3)])
def test_el_limite_de_paginas_de_la_busqueda_tiene_prioridad(db, pedidas, paginas_maximas, esperado):
    bus = {**BUS, "paginas_maximas": paginas_maximas}
    _, n = b.obtener_resultados("wallapop", bus, config_con([bus]), db, False)
    assert n == esperado          # 99 se acota a 5, pero la API solo tiene 4; "x" cae al valor global (3)


def test_si_la_api_ignora_el_cursor_no_se_entra_en_bucle(db, monkeypatch):
    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": lambda q, mx, c, cursor=None: ([anuncio(1), anuncio(2)], "mismo")})
    items, n = b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False)
    assert n == 2 and len(items) == 2


def test_un_fallo_en_la_segunda_pagina_conserva_la_primera(db, monkeypatch):
    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": lambda q, mx, c, cursor=None:
                                          ([anuncio(1)], "BOOM") if cursor is None else (_ for _ in ()).throw(RuntimeError("HTTP 403"))})
    items, n = b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False)
    assert n == 1 and len(items) == 1


def test_un_fallo_en_la_primera_pagina_si_es_error(db, monkeypatch):
    def roto(q, mx, c, cursor=None):
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": roto})
    with pytest.raises(RuntimeError):
        b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False)


def test_un_buscador_que_devuelve_una_lista_sigue_funcionando(db, monkeypatch):
    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": lambda q, mx, c: [anuncio(1)]})
    assert b.obtener_resultados("wallapop", BUS, config_con([BUS]), db, False) == ([anuncio(1)], 1)


def test_consulta_la_variante_pero_mantiene_la_identidad_de_la_busqueda(db, monkeypatch):
    recibidas = []
    monkeypatch.setattr(b, "BUSCADORES", {"wallapop": lambda q, mx, c, cursor=None: recibidas.append(q) or ([], None)})
    b.obtener_resultados("wallapop", {"keyword": "game cube"}, config_con([]), db, False, "gamecube")
    assert recibidas == ["gamecube"]
