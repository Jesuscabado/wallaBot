import pytest

import bot_alertas as b
from helpers import anuncio


def test_norm_ignora_tildes_y_mayusculas():
    assert b.norm("Cámara ÑANDÚ") == "camara nandu"


def test_terminos_busqueda_quita_repetidos_y_vacios():
    assert b.terminos_busqueda({"keyword": "game cube"}) == ["game cube"]
    b_ = {"keyword": "Game Cube", "alias": ["gamecube", "GAMECUBE", "game cube", " ", "gcn", 5]}
    assert b.terminos_busqueda(b_) == ["Game Cube", "gamecube", "gcn"]


BUS = {"keyword": "game cube", "min_precio": 5, "max_precio": 30,
       "excluir": ["Funda", "caja vacía"], "todas_las_palabras": True}


@pytest.mark.parametrize("titulo, precio, pasa", [
    ("Consola Game Cube negra", 25, True),
    ("Consola GameCube", 25, True),            # junto también vale: se compara por fragmentos
    ("Funda Game Cube", 10, False),            # palabra excluida
    ("Game Cube + caja vacia", 20, False),     # exclusión sin tilde
    ("Consola Game negra", 25, False),         # falta «cube»
    ("Game Cube mando", 3, False),             # por debajo del mínimo
    ("Game Cube", 31, False),                  # por encima del máximo
    ("Game Cube", 5, True),                    # límites inclusivos
    ("Game Cube", 30, True),
    ("Game Cube sin precio", None, True),      # sin precio, no se filtra por precio
])
def test_pasa_filtros(titulo, precio, pasa):
    assert b.pasa_filtros(anuncio(1, titulo, precio), BUS) is pasa


def test_sin_todas_las_palabras_basta_una():
    assert b.pasa_filtros(anuncio(1, "Consola Game negra"), {**BUS, "todas_las_palabras": False})


def test_sin_precio_ignora_los_limites():
    assert b.pasa_filtros(anuncio(1, "Game Cube", 999), BUS, con_precio=False)
    assert not b.pasa_filtros(anuncio(1, "Funda Game Cube", 10), BUS, con_precio=False)   # el resto sí cuenta


def test_las_variantes_cuentan_para_todas_las_palabras():
    con_alias = {"keyword": "game cube", "alias": ["gcn"], "todas_las_palabras": True}
    assert b.pasa_filtros(anuncio(1, "Consola GCN negra"), con_alias)      # solo entra por la variante
    assert not b.pasa_filtros(anuncio(1, "Consola GCN negra"), {"keyword": "game cube", "todas_las_palabras": True})
    assert not b.pasa_filtros(anuncio(1, "Consola Game Boy"), con_alias)
