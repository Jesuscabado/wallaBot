import json

import pytest

import bot_alertas as b


def test_se_crea_un_config_de_ejemplo_si_falta(tmp_path):
    cfg = b.cargar_config()
    assert (tmp_path / "config.json").exists()
    assert cfg["busquedas"] == b.CONFIG_DEFECTO["busquedas"]


def test_config_con_error_de_sintaxis_conserva_la_anterior(cfg_json):
    anterior = b.cargar_config()
    cfg_json.escribir("{ roto")
    assert b.cargar_config(anterior) == anterior
    assert cfg_json.ruta.read_text() == "{ roto"            # no se toca el archivo del usuario


def test_config_antigua_se_completa_con_valores_por_defecto(cfg_json):
    cfg_json.escribir({"busquedas": [{"keyword": "x"}]})    # sin ninguna sección nueva
    cfg = b.cargar_config()
    assert cfg["chollos"] == b.CONFIG_DEFECTO["chollos"]
    assert cfg["bajadas_precio"]["min_euros"] == 2
    assert cfg["horario_silencioso"]["activo"] is False
    assert cfg["paginas_maximas"] == 3 and cfg["max_avisos_por_busqueda"] == 8


def test_secciones_parciales_se_mezclan_con_los_valores_por_defecto(cfg_json):
    cfg_json.escribir({"busquedas": [], "chollos": {"porcentaje": 50}})
    assert b.cargar_config()["chollos"] == {**b.CONFIG_DEFECTO["chollos"], "porcentaje": 50}


@pytest.mark.parametrize("valor, esperado", [(99, 5), (0, 1), (-2, 1), (4, 4), ("mucho", 3), (None, 3)])
def test_paginas_maximas_se_acota(cfg_json, valor, esperado):
    cfg_json.escribir({"paginas_maximas": valor, "busquedas": []})
    assert b.cargar_config()["paginas_maximas"] == esperado


@pytest.mark.parametrize("valor, esperado", [(999, 50), (0, 0), (-3, 0), (5, 5), ("mucho", 8), (None, 8)])
def test_max_avisos_se_acota(cfg_json, valor, esperado):
    cfg_json.escribir({"max_avisos_por_busqueda": valor, "busquedas": []})
    assert b.cargar_config()["max_avisos_por_busqueda"] == esperado


def test_la_config_por_defecto_es_json_valido():
    assert json.loads(json.dumps(b.CONFIG_DEFECTO)) == b.CONFIG_DEFECTO


# ---- archivos con una forma inesperada: el bot no debe caerse ----

@pytest.mark.parametrize("contenido", ["[]", '"texto"', "123", "null", "true"])
def test_un_json_valido_que_no_es_un_objeto_se_trata_como_config_rota(cfg_json, contenido):
    anterior = b.cargar_config()
    cfg_json.escribir(contenido)
    assert b.cargar_config(anterior) == anterior


def test_busquedas_no_validas_se_ignoran(cfg_json, capsys):
    cfg_json.escribir({"busquedas": ["zelda", {"keyword": ""}, {"sin": "keyword"}, {"keyword": 5}, {"keyword": "ok"}]})
    assert b.cargar_config()["busquedas"] == [{"keyword": "ok"}]
    assert "ignoro búsquedas no válidas" in capsys.readouterr().out


@pytest.mark.parametrize("valor", ["zelda", 5, None, {"keyword": "x"}])
def test_busquedas_que_no_son_una_lista_se_ignoran(cfg_json, valor):
    cfg_json.escribir({"busquedas": valor})
    assert b.cargar_config()["busquedas"] == []


def test_plataformas_con_forma_incorrecta_vuelven_al_valor_por_defecto(cfg_json):
    cfg_json.escribir({"plataformas": "wallapop", "busquedas": []})
    assert b.cargar_config()["plataformas"] == ["wallapop", "vinted"]


def test_secciones_con_forma_incorrecta_vuelven_a_los_valores_por_defecto(cfg_json):
    cfg_json.escribir({"ubicacion": "madrid", "chollos": 5, "alertas_salud": [], "busquedas": []})
    cfg = b.cargar_config()
    assert cfg["ubicacion"] == b.CONFIG_DEFECTO["ubicacion"] and cfg["chollos"] == b.CONFIG_DEFECTO["chollos"]
    assert cfg["alertas_salud"] == b.CONFIG_DEFECTO["alertas_salud"]


@pytest.mark.parametrize("valor, esperado", [("120", 120), (90.7, 90), ("abc", 180), (None, 180), ([], 180)])
def test_el_intervalo_se_normaliza_a_un_entero(cfg_json, valor, esperado):
    cfg_json.escribir({"intervalo_segundos": valor, "busquedas": []})
    assert b.cargar_config()["intervalo_segundos"] == esperado
