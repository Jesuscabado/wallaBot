import json
import time

import pytest

import bot_alertas as b
from helpers import anuncio, config_con

BUS = {"keyword": "zelda", "max_precio": 30, "excluir": ["funda"]}
LIBRE = {"keyword": "zelda"}                       # sin filtros de precio


def procesar(db, items, bus=LIBRE, primera_vez=False, cfg=None):
    return b.procesar_resultados(db, cfg or config_con([bus]), bus, "wallapop", items, primera_vez)


def precio_guardado(db, i=1):
    return db.execute("SELECT precio FROM anuncios WHERE id=?", (f"wallapop:{i}",)).fetchone()[0]


# ------------------------------ marcas y migración ------------------------------

def test_marca_de_primera_pasada_por_plataforma_y_busqueda(db):
    assert not b.hay_marca(db, "wallapop", "zelda")
    b.poner_marca(db, "wallapop", "zelda")
    assert b.hay_marca(db, "wallapop", "zelda")
    assert not b.hay_marca(db, "vinted", "zelda") and not b.hay_marca(db, "wallapop", "mario")


def test_migra_el_vistos_json_antiguo(db, tmp_path):
    (tmp_path / "vistos.json").write_text(json.dumps({"wallapop|minish cap": ["wallapop:1", "wallapop:2"]}))
    b.migrar_vistos(db)
    assert not (tmp_path / "vistos.json").exists() and (tmp_path / "vistos.json.migrado").exists()
    assert db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 2
    assert b.hay_marca(db, "wallapop", "minish cap")                   # no hay otra primera pasada silenciosa
    assert b.estadisticas_precios(db, "minish cap", 60)["n"] == 0      # sin precio conocido: no cuentan
    b.migrar_vistos(db)                                                # una segunda vez no hace nada


def test_un_vistos_json_ilegible_se_ignora(db, tmp_path):
    (tmp_path / "vistos.json").write_text("{ roto")
    b.migrar_vistos(db)
    assert (tmp_path / "vistos.json").exists() and db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 0


def test_sin_vistos_json_no_hace_nada(db):
    b.migrar_vistos(db)
    assert db.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 0


# ------------------------------- anuncios nuevos -------------------------------

def test_primera_pasada_memoriza_sin_avisar(db):
    avisar, nuevos = procesar(db, [anuncio(1, precio=20), anuncio(2, precio=25)], primera_vez=True)
    assert avisar == [] and nuevos == 2
    assert db.execute("SELECT COUNT(*) FROM anuncios").fetchone()[0] == 2


def test_avisa_de_los_nuevos_que_pasan_los_filtros(db):
    procesar(db, [anuncio(1)], BUS, True)
    avisar, nuevos = procesar(db, [anuncio(1), anuncio(3, "Zelda 3", 20), anuncio(4, "Funda Zelda", 5),
                                   anuncio(5, "Zelda caro", 99)], BUS)
    assert [a["id"] for a, _ in avisar] == ["wallapop:3"] and nuevos == 3      # los filtrados cuentan como vistos
    assert procesar(db, [anuncio(4, "Funda Zelda", 5)], BUS) == ([], 0)         # y no vuelven a salir


def test_actualiza_titulo_y_url_de_los_anuncios_conocidos(db):
    procesar(db, [anuncio(1, "Zelda viejo")], primera_vez=True)
    procesar(db, [{**anuncio(1, "Zelda nuevo"), "url": "https://x/otra"}])
    assert db.execute("SELECT titulo, url FROM anuncios").fetchone() == ("Zelda nuevo", "https://x/otra")


# ------------------------------- bajadas de precio -------------------------------

@pytest.mark.parametrize("antes, ahora, avisa", [
    (20, 12, True),      # −8 € y −40%
    (20, 18, True),      # justo en los umbrales: −2 € y −10 % (inclusivo)
    (100, 85, True),     # −15 € y −15%
    (20, 24, False),     # sube
    (20, 19, False),     # −1 €: por debajo de 2 €
    (100, 95, False),    # −5%: por debajo del 10 %
    (10, 9, False),      # −10 % pero solo 1 €
])
def test_bajadas_de_precio(db, antes, ahora, avisa):
    procesar(db, [anuncio(1, precio=antes)], primera_vez=True)
    avisar, nuevos = procesar(db, [anuncio(1, precio=ahora)])
    assert nuevos == 0 and bool(avisar) is avisa
    assert precio_guardado(db) == ahora                       # el precio se actualiza siempre
    if avisa:
        assert avisar[0][1].startswith("📉") and f"{antes:g} € → {ahora:g} €" in avisar[0][1]


def test_la_nota_de_bajada_indica_el_porcentaje(db):
    procesar(db, [anuncio(1, precio=20)], primera_vez=True)
    assert "−40%" in procesar(db, [anuncio(1, precio=12)])[0][0][1]


def test_un_anuncio_que_baja_y_entra_en_tu_rango_avisa(db):
    procesar(db, [anuncio(9, "Zelda caro", 50)], BUS, True)             # fuera del máximo de 30 €
    assert procesar(db, [anuncio(9, "Zelda caro", 50)], BUS)[0] == []   # sigue igual: nada
    avisar, _ = procesar(db, [anuncio(9, "Zelda caro", 25)], BUS)
    assert len(avisar) == 1 and "50 € → 25 €" in avisar[0][1]


def test_una_bajada_que_sigue_fuera_de_rango_no_avisa(db):
    procesar(db, [anuncio(9, "Zelda caro", 80)], BUS, True)
    assert procesar(db, [anuncio(9, "Zelda caro", 50)], BUS)[0] == []


def test_bajadas_desactivadas_no_avisan_pero_actualizan_el_precio(db):
    cfg = config_con([LIBRE])
    cfg["bajadas_precio"]["activo"] = False
    procesar(db, [anuncio(1, precio=20)], cfg=cfg, primera_vez=True)
    assert procesar(db, [anuncio(1, precio=5)], cfg=cfg)[0] == [] and precio_guardado(db) == 5


def test_el_historial_registra_cada_cambio_de_precio(db):
    procesar(db, [anuncio(1, precio=20)], primera_vez=True)
    procesar(db, [anuncio(1, precio=20)])                                # sin cambios: no se anota
    procesar(db, [anuncio(1, precio=15)])
    filas = db.execute("SELECT precio FROM historial WHERE id='wallapop:1' ORDER BY rowid").fetchall()
    assert [f[0] for f in filas] == [20, 15]


def test_un_precio_desconocido_se_aprende_sin_avisar(db):
    db.execute("INSERT INTO anuncios (id, busqueda, plataforma, relevante, primera, ultima) "
               "VALUES ('wallapop:1', 'zelda', 'wallapop', 0, 1, 1)")      # como los migrados de vistos.json
    assert procesar(db, [anuncio(1, precio=10)])[0] == [] and precio_guardado(db) == 10


# ---------------------------------- chollos ----------------------------------

def muestra(db, bus=LIBRE, n=20, base=40, extra=()):
    """n anuncios con precios base..base+n-1 (mediana 49.5 con los valores por defecto)."""
    procesar(db, [anuncio(100 + i, f"Zelda {i}", base + i) for i in range(n)] + list(extra), bus, True)


def test_chollo_marca_un_precio_muy_por_debajo_de_la_mediana(db):
    muestra(db)
    avisar, _ = procesar(db, [anuncio(300, "Zelda barato", 25), anuncio(301, "Zelda normal", 45),
                              anuncio(302, "Zelda justo", 34)])
    notas = {a["id"]: n for a, n in avisar}
    assert notas["wallapop:300"].startswith("🔥") and "49.5 €" in notas["wallapop:300"]
    assert "49% por debajo" in notas["wallapop:300"] and "20 anuncios" in notas["wallapop:300"]
    assert notas["wallapop:301"] is None
    assert notas["wallapop:302"].startswith("🔥")                      # 34 ≤ 49.5 × 0.7 = 34.65


def test_el_chollo_necesita_suficientes_muestras(db):
    muestra(db, n=5)
    assert procesar(db, [anuncio(300, "Zelda regalado", 5)])[0][0][1] is None


def test_los_excluidos_no_cuentan_para_las_estadisticas(db):
    bus = {"keyword": "zelda", "excluir": ["funda"]}
    muestra(db, bus, extra=[anuncio(200 + i, f"Funda Zelda {i}", 3) for i in range(10)])
    assert b.estadisticas_precios(db, "zelda", 60) == {"n": 20, "mediana": 49.5, "min": 40, "max": 59}


@pytest.mark.parametrize("pct, es_chollo", [(0, False), (60, False), (10, True)])
def test_el_umbral_de_chollo_se_ajusta_por_busqueda(db, pct, es_chollo):
    bus = {"keyword": "zelda", "chollo_porcentaje": pct}               # 0 desactiva; 40 € vs mediana 49.5
    muestra(db, bus)
    nota = procesar(db, [anuncio(300, "Zelda ocasión", 40)], bus)[0][0][1]
    assert bool(nota and nota.startswith("🔥")) is es_chollo


def test_chollos_desactivados_globalmente(db):
    cfg = config_con([LIBRE])
    cfg["chollos"]["activo"] = False
    muestra(db)
    assert procesar(db, [anuncio(300, "Zelda regalado", 5)], cfg=cfg)[0][0][1] is None


def test_las_estadisticas_solo_miran_la_ventana_de_dias(db):
    muestra(db)
    db.execute("UPDATE anuncios SET ultima=? WHERE id='wallapop:100'", (time.time() - 90 * 86400,))
    assert b.estadisticas_precios(db, "zelda", 60)["n"] == 19
    assert b.estadisticas_precios(db, "zelda", 120)["n"] == 20


def test_estadisticas_sin_datos(db):
    assert b.estadisticas_precios(db, "zelda", 60) == {"n": 0, "mediana": None, "min": None, "max": None}


def test_las_estadisticas_son_por_busqueda(db):
    muestra(db)
    assert b.estadisticas_precios(db, "mario", 60)["n"] == 0


# ---------------------------------- limpieza ----------------------------------

def test_limpiar_borra_lo_que_lleva_mucho_sin_aparecer(db):
    procesar(db, [anuncio(1), anuncio(2)], primera_vez=True)
    viejo = time.time() - 200 * 86400
    db.execute("UPDATE anuncios SET ultima=? WHERE id='wallapop:1'", (viejo,))
    db.execute("UPDATE historial SET t=? WHERE id='wallapop:1'", (viejo,))
    b.limpiar_db(db)
    assert [f[0] for f in db.execute("SELECT id FROM anuncios")] == ["wallapop:2"]
    assert [f[0] for f in db.execute("SELECT id FROM historial")] == ["wallapop:2"]
