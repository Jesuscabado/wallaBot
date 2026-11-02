"""El hilo que atiende los comandos de Telegram, con una API de Telegram simulada."""
import json

import pytest

import bot_alertas as b
from helpers import Fin


class R:
    def __init__(self, datos):
        self.datos = datos

    def json(self):
        return self.datos


def upd(i, chat, texto):
    return {"update_id": i, "message": {"chat": {"id": chat}, "text": texto}}


def telegram_falso(monkeypatch, respuestas):
    """Cada llamada a getUpdates devuelve la siguiente respuesta (o lanza si es una excepción).
    Cuando se acaban, corta el bucle infinito con Fin."""
    gets, posts, pendientes = [], [], list(respuestas)

    def fake_get(url, params=None, timeout=None):
        gets.append((url, dict(params or {})))
        if not pendientes:
            raise Fin
        r = pendientes.pop(0)
        if isinstance(r, Exception):
            raise r
        return R(r)

    def fake_post(url, **kwargs):
        posts.append((url, kwargs))
        return R({"ok": True})

    monkeypatch.setattr(b.requests, "get", fake_get)
    monkeypatch.setattr(b.requests, "post", fake_post)
    return gets, posts


def test_solo_obedece_al_chat_autorizado_y_descarta_lo_antiguo(monkeypatch, telegram, estado, cfg_json):
    cfg_json.escribir({"busquedas": [{"keyword": "x"}]})
    gets, posts = telegram_falso(monkeypatch, [
        {"ok": True, "result": [upd(10, 111, "/quitar 1")]},                              # mensaje antiguo: se descarta
        {"ok": True, "result": [upd(11, 999, "/anadir intruso 5"), upd(12, 111, "/anadir zelda 30")]},
    ])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert [x["keyword"] for x in cfg_json.leer()["busquedas"]] == ["x", "zelda"]         # ni intruso ni el /quitar antiguo
    assert len(telegram.mensajes) == 1 and "Añadida" in telegram.mensajes[0]
    assert gets[1][1]["offset"] == 11 and gets[2][1]["offset"] == 13                       # avanza el offset
    assert gets[1][1]["timeout"] == 30 and json.loads(gets[1][1]["allowed_updates"]) == ["message"]


def test_registra_el_menu_de_comandos(monkeypatch, telegram, estado):
    _, posts = telegram_falso(monkeypatch, [{"ok": True, "result": []}])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    (url, kwargs), = [p for p in posts if "setMyCommands" in p[0]]
    comandos = {c["command"] for c in json.loads(kwargs["data"]["commands"])}
    assert comandos == {c for c, _ in b.MENU} and "diagnostico" in comandos
    assert b.TELEGRAM_TOKEN in url                              # la URL lleva el token: nunca se imprime


def test_si_telegram_responde_error_espera_y_lo_avisa(monkeypatch, telegram, estado, capsys):
    telegram_falso(monkeypatch, [{"ok": True, "result": []}, {"ok": False, "description": "Conflict"}])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert "otra copia del bot" in capsys.readouterr().out


def test_un_error_de_red_no_mata_el_hilo(monkeypatch, telegram, estado, capsys):
    telegram_falso(monkeypatch, [{"ok": True, "result": []}, RuntimeError("sin red"), {"ok": True, "result": []}])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert "[comandos] error" in capsys.readouterr().out


def test_si_falla_el_arranque_sigue_escuchando(monkeypatch, telegram, estado, capsys):
    telegram_falso(monkeypatch, [RuntimeError("sin red")])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert "aviso al iniciar" in capsys.readouterr().out


def test_ignora_mensajes_sin_texto_y_actualizaciones_raras(monkeypatch, telegram, estado, cfg_json):
    cfg_json.escribir({"busquedas": []})
    telegram_falso(monkeypatch, [
        {"ok": True, "result": []},
        {"ok": True, "result": [{"update_id": 5}, {"update_id": 6, "message": {"chat": {"id": 111}}},
                                {"update_id": 7, "message": {"chat": {"id": 111}, "text": "hola"}}]},
    ])
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert telegram.mensajes == []


def test_el_id_del_chat_se_compara_como_texto(monkeypatch, telegram, estado, cfg_json):
    cfg_json.escribir({"busquedas": []})
    telegram_falso(monkeypatch, [{"ok": True, "result": []},
                                 {"ok": True, "result": [upd(1, "111", "/lista")]}])     # llega como cadena
    with pytest.raises(Fin):
        b.escuchar_comandos(estado)
    assert len(telegram.mensajes) == 1
