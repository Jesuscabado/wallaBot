import json

import pytest

import bot_alertas as b
from helpers import anuncio, config_con

DOS = {"busquedas": [{"keyword": "game cube", "max_precio": 30}, {"keyword": "zelda", "max_precio": 50}]}


@pytest.fixture
def dos(cfg_json):
    cfg_json.escribir(DOS)
    return cfg_json


# --------------------------------- generales ---------------------------------

def test_los_mensajes_que_no_son_comandos_se_ignoran(cmd):
    assert cmd("hola") is None and cmd("") is None and cmd("   ") is None


@pytest.mark.parametrize("texto", ["/ayuda", "/start", "/help", "/ayuda@MiBot", "/AYUDA"])
def test_ayuda(cmd, texto):
    assert cmd(texto) == b.AYUDA


def test_comando_desconocido(cmd):
    assert "No conozco" in cmd("/loquesea")


# --------------------------------- añadir y editar ---------------------------------

def test_anadir_con_precio_maximo(cmd, dos):
    r = cmd("/anadir zelda minish cap 30")
    assert "Añadida" in r and "(máx 30 €)" in r
    assert dos.leer()["busquedas"][-1] == {"keyword": "zelda minish cap", "min_precio": None, "max_precio": 30,
                                           "excluir": [], "todas_las_palabras": False}
    assert b.DESPERTAR.is_set()


def test_anadir_con_tilde_y_decimales(cmd, dos):
    cmd("/añadir game boy 2,5")
    assert dos.leer()["busquedas"][-1]["max_precio"] == 2.5


def test_anadir_con_sin_deja_el_numero_en_el_nombre(cmd, dos):
    cmd("/anadir ps 5 sin")
    assert dos.leer()["busquedas"][-1]["keyword"] == "ps 5" and dos.leer()["busquedas"][-1]["max_precio"] is None


def test_anadir_duplicado_o_sin_texto(cmd, dos):
    assert "Ya vigilas" in cmd("/anadir ZELDA")
    assert "Uso" in cmd("/anadir")


def test_anadir_sugiere_la_variante_junta_solo_con_dos_palabras(cmd, dos):
    assert "/alias 3 gameboy" in cmd("/anadir game boy 40")
    assert "/alias" not in cmd("/anadir zelda minish cap 30")
    assert "/alias" not in cmd("/anadir nintendo")


def test_la_lista_escapa_el_html(cmd, dos):
    cmd("/anadir ps5 <b>hack</b> 10")
    assert "&lt;b&gt;hack&lt;/b&gt;" in cmd("/lista")


def test_lista_vacia(cmd, cfg_json):
    cfg_json.escribir({"busquedas": []})
    assert "No tienes búsquedas" in cmd("/lista")


def test_maximo_y_minimo(cmd, dos):
    assert "máximo 45 €" in cmd("/maximo 1 45") and dos.leer()["busquedas"][0]["max_precio"] == 45
    cmd("/minimo 1 5€")
    assert dos.leer()["busquedas"][0]["min_precio"] == 5
    assert "sin límite" in cmd("/minimo 1 sin") and dos.leer()["busquedas"][0]["min_precio"] is None


def test_excluir_y_permitir_ignoran_tildes_y_duplicados(cmd, dos):
    cmd("/excluir 1 caja vacía")
    cmd("/excluir 1 Caja Vacia")
    cmd("/excluir 1 funda")
    assert dos.leer()["busquedas"][0]["excluir"] == ["caja vacía", "funda"]
    cmd("/permitir 1 CAJA VACIA")
    assert dos.leer()["busquedas"][0]["excluir"] == ["funda"]
    assert "sin: funda" in cmd("/lista") and "máx 30" in cmd("/lista")


def test_quitar(cmd, dos):
    assert "Quitada" in cmd("/quitar 1") and [x["keyword"] for x in dos.leer()["busquedas"]] == ["zelda"]


@pytest.mark.parametrize("texto, esperado", [
    ("/quitar 99", "No existe la búsqueda 99"), ("/quitar", "Indica el número"), ("/quitar abc", "Indica el número"),
    ("/maximo 1 barato", "no es un precio"), ("/maximo 1", "Uso"), ("/excluir 1", "Uso"), ("/permitir 1", "Uso"),
])
def test_errores_de_uso(cmd, dos, texto, esperado):
    assert esperado in cmd(texto)


def test_un_config_roto_no_se_pisa(cmd, cfg_json):
    cfg_json.escribir("{ roto")
    assert "error de sintaxis" in cmd("/anadir algo 10")
    assert cfg_json.ruta.read_text() == "{ roto"


@pytest.mark.parametrize("contenido", ["[]", '"texto"'])
def test_un_config_que_no_es_un_objeto_tampoco_se_pisa(cmd, cfg_json, contenido):
    cfg_json.escribir(contenido)
    assert "objeto JSON" in cmd("/anadir algo 10") and cfg_json.ruta.read_text() == contenido


# ---------------------------------- variantes ----------------------------------

def test_alias_y_quitaralias(cmd, dos):
    assert "también busco «gamecube»" in cmd("/alias 1 gamecube") and dos.leer()["busquedas"][0]["alias"] == ["gamecube"]
    assert b.DESPERTAR.is_set()
    assert "ya está" in cmd("/alias 1 GameCube") and "ya está" in cmd("/alias 1 Game Cube")
    cmd("/alias 1 gcn")
    cmd("/alias 1 game-cube")
    assert "Máximo 3" in cmd("/alias 1 otra") and dos.leer()["busquedas"][0]["alias"] == ["gamecube", "gcn", "game-cube"]
    assert "también: gamecube, gcn, game-cube" in cmd("/lista")
    assert "ya no busco" in cmd("/quitaralias 1 GCN") and dos.leer()["busquedas"][0]["alias"] == ["gamecube", "game-cube"]
    assert "no es una variante" in cmd("/quitaralias 1 nada")
    cmd("/quitaralias 1 gamecube")
    cmd("/quitaralias 1 game-cube")
    assert "alias" not in dos.leer()["busquedas"][0]                         # lista vacía: se borra la clave


def test_alias_errores_de_uso(cmd, dos):
    assert "Uso" in cmd("/alias 1") and "No existe" in cmd("/alias 9 x") and "Indica el número" in cmd("/alias")


# ------------------------------ pausa y horario silencioso ------------------------------

def test_pausa_con_horas(cmd, estado, reloj):
    reloj.t = 1000.0
    assert "hasta las" in cmd("/pausa 2") and estado["silencio_hasta"] == 1000.0 + 7200
    assert json.loads(open("estado.json").read())["silencio_hasta"] == estado["silencio_hasta"]
    assert b.silenciado(b.cargar_config(), estado)


def test_pausa_indefinida_y_reanudar(cmd, estado):
    assert "/reanudar" in cmd("/pausa") and estado["silencio_hasta"] == b.INDEFINIDO
    assert "reanudados" in cmd("/reanudar") and estado["silencio_hasta"] == 0 and b.DESPERTAR.is_set()


def test_reanudar_con_pendientes_avisa_de_que_los_enviara(cmd, estado):
    estado["pendientes"] = [{"plataforma": "W", "titulo": "t", "precio": 1, "url": "u", "kw": "k", "nota": None}]
    assert "lo acumulado" in cmd("/reanudar")


def test_reanudar_dentro_del_horario_silencioso_lo_dice(cmd, estado, cfg_json, reloj):
    import time
    reloj.t = time.mktime((2026, 10, 27, 3, 30, 0, 0, 0, -1))
    cfg_json.escribir({"busquedas": [], "horario_silencioso": {"activo": True, "desde": 1, "hasta": 8}})
    assert "horario silencioso" in cmd("/reanudar") and not b.DESPERTAR.is_set()


@pytest.mark.parametrize("texto, esperado", [("/pausa 0", "entre 0 y 720"), ("/pausa 721", "entre 0 y 720"), ("/pausa mucho", "Uso")])
def test_pausa_errores(cmd, texto, esperado):
    assert esperado in cmd(texto)


def test_silencio(cmd, cfg_json):
    cfg_json.escribir({"busquedas": []})
    assert "Sin horario" in cmd("/silencio")
    assert "de 1:00 a 8:00" in cmd("/silencio 1-8") and cfg_json.leer()["horario_silencioso"] == {"activo": True, "desde": 1, "hasta": 8}
    cmd("/silencio 22-6")
    assert cfg_json.leer()["horario_silencioso"]["desde"] == 22 and "22:00 a 6:00" in cmd("/silencio")
    cmd("/silencio 22-24")
    assert cfg_json.leer()["horario_silencioso"]["hasta"] == 0
    assert "desactivado" in cmd("/silencio off") and cfg_json.leer()["horario_silencioso"]["activo"] is False


@pytest.mark.parametrize("texto", ["/silencio 5-5", "/silencio 30-2", "/silencio 24-3", "/silencio 3-25", "/silencio raro", "/silencio 24-24"])
def test_silencio_valores_no_validos(cmd, cfg_json, texto):
    cfg_json.escribir({"busquedas": []})
    assert "no válidas" in cmd(texto) or "Uso" in cmd(texto)


# ---------------------------- chollos, bajadas, páginas, límite ----------------------------

def test_chollos(cmd, dos):
    assert "activos" in cmd("/chollos")
    assert "25%" in cmd("/chollos 25") and dos.leer()["chollos"]["porcentaje"] == 25
    cmd("/chollos off")
    assert dos.leer()["chollos"]["activo"] is False
    cmd("/chollos on")
    assert dos.leer()["chollos"]["activo"] is True
    assert "game cube" in cmd("/chollos 1 40") and dos.leer()["busquedas"][0]["chollo_porcentaje"] == 40
    cmd("/chollos 1 off")
    assert dos.leer()["busquedas"][0]["chollo_porcentaje"] == 0
    assert "global" in cmd("/chollos 1 auto") and "chollo_porcentaje" not in dos.leer()["busquedas"][0]


@pytest.mark.parametrize("texto", ["/chollos 99", "/chollos 0", "/chollos mucho", "/chollos 1 100"])
def test_chollos_porcentaje_no_valido(cmd, dos, texto):
    assert "porcentaje válido" in cmd(texto)


def test_chollos_busqueda_inexistente(cmd, dos):
    assert "No existe" in cmd("/chollos 5 20")


def test_bajadas(cmd, dos):
    assert "activas" in cmd("/bajadas")
    cmd("/bajadas off")
    assert dos.leer()["bajadas_precio"]["activo"] is False
    cmd("/bajadas 15")
    assert dos.leer()["bajadas_precio"] == {"activo": True, "min_porcentaje": 15, "min_euros": 2}
    assert "porcentaje válido" in cmd("/bajadas 100")


def test_paginas(cmd, dos):
    assert "3" in cmd("/paginas") and "máximas por búsqueda: 4" in cmd("/paginas 4") and dos.leer()["paginas_maximas"] == 4
    assert "hasta 2" in cmd("/paginas 1 2") and dos.leer()["busquedas"][0]["paginas_maximas"] == 2
    assert "global" in cmd("/paginas 1 auto") and "paginas_maximas" not in dos.leer()["busquedas"][0]
    assert "del 1 al 5" in cmd("/paginas 9") and "del 1 al 5" in cmd("/paginas 1 0") and "No existe" in cmd("/paginas 7 2")


def test_limite(cmd, dos):
    assert "8 avisos" in cmd("/limite")
    assert "5" in cmd("/limite 5") and dos.leer()["max_avisos_por_busqueda"] == 5
    assert "sin límite" in cmd("/limite 0") and "sin límite" in cmd("/limite")
    assert "hasta 3 avisos" in cmd("/limite 1 3") and dos.leer()["busquedas"][0]["max_avisos"] == 3
    assert "sin límite" in cmd("/limite 2 0") and dos.leer()["busquedas"][1]["max_avisos"] == 0
    assert "global" in cmd("/limite 1 auto") and "max_avisos" not in dos.leer()["busquedas"][0]


@pytest.mark.parametrize("texto", ["/limite 51", "/limite -1", "/limite mucho", "/limite 1 99"])
def test_limite_no_valido(cmd, dos, texto):
    assert "0 al 50" in cmd(texto)


# ----------------------------------- precios y estado -----------------------------------

def test_precios(cmd, dos, db):
    items = [anuncio(i, f"Zelda {i}", 40 + i) for i in range(20)]
    b.procesar_resultados(db, config_con([{"keyword": "zelda"}]), {"keyword": "zelda"}, "wallapop", items, True)
    r = cmd("/precios 2")
    assert "20 anuncios" in r and "mediana 49.5 €" in r and "mín 40 €" in r and "máx 59 €" in r
    assert "Chollo: hasta 34.65 €" in r
    cmd("/chollos 25")
    assert "Chollo: hasta 37.12 €" in cmd("/precios 2") or "Chollo: hasta 37.13 €" in cmd("/precios 2")


def test_precios_sin_datos_y_con_pocas_muestras(cmd, dos, db):
    assert "todavía no tengo" in cmd("/precios 1")
    b.procesar_resultados(db, config_con([]), {"keyword": "zelda"}, "wallapop", [anuncio(1)], True)
    assert "aún no se usa: hacen falta 15" in cmd("/precios 2")
    assert "Indica el número" in cmd("/precios")


def test_estado(cmd, dos, estado, monkeypatch):
    estado["salud"]["vinted"] = {"fallos": 2, "alertado": False}
    estado["pendientes"] = [{}]
    r = cmd("/estado")
    assert "2 búsquedas" in r and "cada ~3 min" in r and "vinted: ⚠️ fallando (2" in r and "wallapop: OK" in r
    assert "1 anuncios guardados" in r and "no configurado" in r
    monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/secreto")
    r = cmd("/estado")
    assert "activado" in r and "secreto" not in r                          # nunca muestra la URL


def test_estado_muestra_pausa_y_horario(cmd, cfg_json, estado):
    cfg_json.escribir({"busquedas": [], "horario_silencioso": {"activo": True, "desde": 1, "hasta": 8}})
    estado["silencio_hasta"] = b.INDEFINIDO
    r = cmd("/estado")
    assert "en pausa" in r and "1:00–8:00" in r
