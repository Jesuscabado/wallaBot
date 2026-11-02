import time

import pytest

import bot_alertas as b
from helpers import config_con, nuevo_estado

CFG = config_con([])                       # alertas tras 3 fallos seguidos


def test_alerta_tras_n_fallos_seguidos_y_solo_una_vez(telegram, estado):
    for _ in range(3):
        b.evaluar_salud("vinted", 0, 3, "HTTP 404 <x>", CFG, estado)
    alertas = [m for m in telegram.mensajes if "fallando" in m]
    assert len(alertas) == 1 and "&lt;x&gt;" in alertas[0] and "/diagnostico" in alertas[0]
    b.evaluar_salud("vinted", 0, 3, "otro", CFG, estado)
    assert len([m for m in telegram.mensajes if "fallando" in m]) == 1          # no se repite


def test_no_alerta_antes_del_umbral(telegram, estado):
    for _ in range(2):
        b.evaluar_salud("vinted", 0, 3, "HTTP 404", CFG, estado)
    assert telegram.mensajes == [] and estado["salud"]["vinted"]["fallos"] == 2


def test_avisa_al_recuperarse_y_limpia_la_pausa(telegram, estado):
    for _ in range(3):
        b.evaluar_salud("vinted", 0, 3, "e", CFG, estado)
    telegram.limpiar()
    b.evaluar_salud("vinted", 2, 0, "", CFG, estado)
    assert telegram.mensajes == ["✅ <b>vinted</b> vuelve a funcionar"]
    assert "vinted" not in estado["pausa_hasta"] and estado["salud"]["vinted"] == {"fallos": 0, "alertado": False}


def test_recuperarse_sin_haber_alertado_no_manda_nada(telegram, estado):
    b.evaluar_salud("vinted", 0, 3, "e", CFG, estado)
    b.evaluar_salud("vinted", 2, 0, "", CFG, estado)
    assert telegram.mensajes == []


def test_un_fallo_parcial_no_cuenta(telegram, estado):
    b.evaluar_salud("wallapop", 1, 1, "x", CFG, estado)
    assert estado["salud"]["wallapop"]["fallos"] == 0 and "wallapop" not in estado["pausa_hasta"]


def test_sin_intentos_no_cambia_nada(telegram, estado):
    b.evaluar_salud("wallapop", 0, 0, "", CFG, estado)
    assert estado["salud"]["wallapop"]["fallos"] == 0 and telegram.mensajes == []


@pytest.mark.parametrize("fallos, espera", [(1, 180), (3, 540), (10, 1800), (50, 1800)])
def test_la_pausa_crece_con_los_fallos_y_tiene_tope(telegram, estado, reloj, fallos, espera):
    reloj.t = 1000.0
    for _ in range(fallos):
        b.evaluar_salud("vinted", 0, 1, "e", CFG, estado)
    assert estado["pausa_hasta"]["vinted"] == 1000.0 + espera


# ---------------------------------- resumen diario ----------------------------------

def hora(h):
    return time.mktime((2026, 10, 27, h, 0, 0, 0, 0, -1))


def cfg_latido(hora_latido=9, activo=True):
    return config_con([], alertas_salud={"fallos_seguidos": 3, "latido_diario": activo, "hora_latido": hora_latido})


def test_resumen_diario_una_vez_al_dia(telegram, reloj):
    reloj.t = hora(10)
    estado = nuevo_estado(ultimo_latido="2000-01-01", avisos=7, ciclos=300)
    estado["salud"]["vinted"] = {"fallos": 2, "alertado": False}
    cfg = config_con([], plataformas=["wallapop", "vinted"],
                     alertas_salud={"fallos_seguidos": 3, "latido_diario": True, "hora_latido": 9})
    b.latido(cfg, estado)
    assert len(telegram.mensajes) == 1
    m = telegram.mensajes[0]
    assert "Sigo vivo" in m and "7 avisos" in m and "300 ciclos" in m and "wallapop: OK" in m and "vinted: ⚠️ fallando" in m
    assert estado["ultimo_latido"] == "2026-10-27" and estado["avisos"] == 0 and estado["ciclos"] == 0
    b.latido(cfg, estado)
    assert len(telegram.mensajes) == 1                                     # el mismo día no se repite


def test_el_resumen_diario_espera_a_su_hora(telegram, reloj):
    reloj.t = hora(8)
    b.latido(cfg_latido(hora_latido=9), nuevo_estado(ultimo_latido="2000-01-01"))
    assert telegram.mensajes == []


def test_el_resumen_diario_se_puede_desactivar(telegram, reloj):
    reloj.t = hora(12)
    b.latido(cfg_latido(activo=False), nuevo_estado(ultimo_latido="2000-01-01"))
    assert telegram.mensajes == []
