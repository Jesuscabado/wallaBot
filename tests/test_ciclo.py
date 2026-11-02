"""El ciclo completo: buscar, filtrar, memorizar y avisar (con un buscador falso)."""
import bot_alertas as b
from helpers import anuncio, config_con, url


def test_primera_pasada_memoriza_y_despues_avisa(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda"}])
    web.datos = {"zelda": [anuncio(1)]}
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == []
    web.datos = {"zelda": [anuncio(2, "Zelda Link", 12.5), anuncio(1)]}
    b.ciclo(cfg, db, estado)
    assert len(telegram.mensajes) == 1
    aviso = telegram.mensajes[0]
    assert url(2) in aviso and "Wallapop" in aviso and "Zelda Link" in aviso and "12.5 €" in aviso and "«zelda»" in aviso
    telegram.limpiar()
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == []                                   # el siguiente ciclo no repite
    assert estado["avisos"] == 1


def test_una_primera_pasada_vacia_tambien_cuenta(db, web, telegram, estado):
    cfg = config_con([{"keyword": "vacia"}])
    b.ciclo(cfg, db, estado)
    web.datos = {"vacia": [anuncio(1)]}
    b.ciclo(cfg, db, estado)
    assert len(telegram.mensajes) == 1


def test_el_aviso_lleva_la_foto_y_escapa_el_html(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda"}])
    b.ciclo(cfg, db, estado)
    web.datos = {"zelda": [{**anuncio(1, "Zelda <b>&</b>"), "foto": "https://img/1.jpg"}]}
    b.ciclo(cfg, db, estado)
    assert telegram.fotos == ["https://img/1.jpg"] and "&lt;b&gt;&amp;&lt;/b&gt;" in telegram.mensajes[0]


def test_aplica_los_filtros_de_la_busqueda(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda", "max_precio": 30, "excluir": ["funda"]}])
    b.ciclo(cfg, db, estado)
    web.datos = {"zelda": [anuncio(1, "Zelda bueno", 20), anuncio(2, "Funda Zelda", 5), anuncio(3, "Zelda caro", 99)]}
    b.ciclo(cfg, db, estado)
    assert len(telegram.mensajes) == 1 and url(1) in telegram.mensajes[0]


def test_el_ciclo_actualiza_el_pulso_del_vigilante(db, web, estado):
    b.ciclo(config_con([{"keyword": "zelda"}]), db, estado)
    assert estado["pulso"] > 0


# --------------------------------- variantes ---------------------------------

def test_las_variantes_consultan_cada_termino_y_comparten_memoria(db, web, telegram, estado):
    bus = {"keyword": "game cube", "max_precio": 100}
    cfg = config_con([bus])
    web.datos = {"game cube": [anuncio(1, "Game Cube A", 40)]}
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == [] and web.consultas == ["game cube"]

    bus["alias"] = ["gamecube"]                        # añado una variante a una búsqueda que ya existía
    web.consultas.clear()
    web.datos = {"game cube": [anuncio(2, "Game Cube B", 30), anuncio(1, "Game Cube A", 40)],
                 "gamecube": [anuncio(10, "GameCube vieja", 50), anuncio(11, "GameCube otra", 60)]}
    b.ciclo(cfg, db, estado)
    assert web.consultas == ["game cube", "gamecube"]
    assert len(telegram.mensajes) == 1 and url(2) in telegram.mensajes[0]   # lo ya existente en la variante solo se memoriza

    telegram.limpiar()
    web.datos = {"game cube": [anuncio(20, "Game Cube C", 35), anuncio(2, "Game Cube B", 30)],
                 "gamecube": [anuncio(20, "Game Cube C", 35), anuncio(30, "GameCube nueva", 45), anuncio(10, "GameCube vieja", 50)]}
    b.ciclo(cfg, db, estado)
    todo = telegram.todo
    assert len(telegram.mensajes) == 2 and url(20) in todo and url(30) in todo
    assert todo.count(url(20)) == 1                                           # por las dos vías, un solo aviso
    assert db.execute("SELECT COUNT(DISTINCT busqueda) FROM anuncios").fetchone()[0] == 1
    assert b.estadisticas_precios(db, "game cube", 60)["n"] == 6              # estadísticas conjuntas


def test_una_variante_no_comparte_marca_con_otra_busqueda_del_mismo_nombre(db, web, telegram, estado):
    """Si la variante «gamecube» de una búsqueda coincide con OTRA búsqueda llamada «gamecube», la
    primera pasada silenciosa de la variante no debe darse por hecha (y soltar un aluvión de avisos)."""
    a = {"keyword": "game cube"}
    cfg = config_con([a, {"keyword": "gamecube"}])
    web.datos = {"game cube": [], "gamecube": [anuncio(10, "GameCube A"), anuncio(11, "GameCube B")]}
    b.ciclo(cfg, db, estado)                                   # la búsqueda «gamecube» memoriza sus anuncios
    a["alias"] = ["gamecube"]                                  # ahora «game cube» también consulta «gamecube»
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == []                             # para «game cube», esa variante es nueva: solo memoriza


def test_si_falla_una_variante_no_es_fallo_de_la_plataforma(db, web, telegram, estado):
    cfg = config_con([{"keyword": "game cube", "alias": ["gamecube"]}])
    web.datos = {"game cube": [anuncio(1)], "gamecube": RuntimeError("HTTP 403")}
    b.ciclo(cfg, db, estado)
    assert estado["salud"]["wallapop"]["fallos"] == 0
    web.datos = {"game cube": RuntimeError("HTTP 403"), "gamecube": [anuncio(1)]}
    b.ciclo(cfg, db, estado)
    assert estado["salud"]["wallapop"]["fallos"] == 0


def test_si_fallan_todas_las_variantes_cuenta_como_fallo(db, web, telegram, estado):
    cfg = config_con([{"keyword": "game cube", "alias": ["gamecube"]}])
    web.datos = {"game cube": RuntimeError("HTTP 403"), "gamecube": RuntimeError("HTTP 403")}
    b.ciclo(cfg, db, estado)
    assert estado["salud"]["wallapop"]["fallos"] == 1


def test_sin_variantes_hace_una_sola_consulta(db, web, estado):
    b.ciclo(config_con([{"keyword": "zelda"}]), db, estado)
    assert web.consultas == ["zelda"]


# ------------------------------ salud de la plataforma ------------------------------

def test_tres_ciclos_fallando_avisan_una_vez_y_se_avisa_al_recuperarse(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda"}])
    web.datos = {"zelda": RuntimeError("HTTP 403")}
    for _ in range(4):
        estado["pausa_hasta"].clear()                    # sin esperar la pausa creciente
        b.ciclo(cfg, db, estado)
    alertas = [m for m in telegram.mensajes if "fallando" in m]
    assert len(alertas) == 1 and "HTTP 403" in alertas[0] and "/diagnostico" in alertas[0]

    web.datos = {"zelda": [anuncio(1)]}
    estado["pausa_hasta"].clear()
    b.ciclo(cfg, db, estado)
    assert any("vuelve a funcionar" in m for m in telegram.mensajes)


def test_una_plataforma_en_pausa_por_fallos_se_salta(db, web, estado, reloj):
    reloj.t = 1000.0
    estado["pausa_hasta"]["wallapop"] = 2000.0
    b.ciclo(config_con([{"keyword": "zelda"}]), db, estado)
    assert web.consultas == []
    reloj.t = 2500.0                                      # pasada la pausa, vuelve a buscar
    b.ciclo(config_con([{"keyword": "zelda"}]), db, estado)
    assert web.consultas == ["zelda"]


def test_una_plataforma_desconocida_se_ignora(db, web, estado):
    b.ciclo(config_con([{"keyword": "zelda"}], plataformas=["ebay"]), db, estado)
    assert web.consultas == []


# ------------------------------ silencio y límite ------------------------------

def test_en_silencio_acumula_y_al_terminar_manda_un_resumen(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda"}])
    b.ciclo(cfg, db, estado)
    estado["silencio_hasta"] = b.INDEFINIDO
    web.datos = {"zelda": [anuncio(2), anuncio(3)]}
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == [] and len(estado["pendientes"]) == 2
    estado["silencio_hasta"] = 0
    b.ciclo(cfg, db, estado)
    assert len(telegram.mensajes) == 1 and "2 anuncios" in telegram.mensajes[0]
    assert url(2) in telegram.mensajes[0] and url(3) in telegram.mensajes[0] and estado["pendientes"] == []


def test_el_limite_de_avisos_se_aplica_por_busqueda(db, web, telegram, estado):
    cfg = config_con([{"keyword": "zelda"}, {"keyword": "mario", "max_avisos": 2}])
    b.ciclo(cfg, db, estado)                                                       # memoriza
    web.datos = {"zelda": [anuncio(1000 + i, f"Zelda {i}") for i in range(20)],
                 "mario": [anuncio(2000 + i, f"Mario {i}") for i in range(5)]}
    b.ciclo(cfg, db, estado)
    zelda = [m for m in telegram.mensajes if "Zelda" in m or "«zelda»" in m]
    mario = [m for m in telegram.mensajes if "Mario" in m or "«mario»" in m]
    assert len(zelda) == 9 and "+12 anuncios más" in " ".join(zelda)               # 8 sueltos (global) + 1 resumen
    assert len(mario) == 3 and "+3 anuncios más" in " ".join(mario)                # 2 sueltos (propio) + 1 resumen
    assert estado["avisos"] == 25
    telegram.limpiar()
    b.ciclo(cfg, db, estado)
    assert telegram.mensajes == []                                                 # nada se repite
