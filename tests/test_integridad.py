"""Coherencia entre comandos, ayuda, menú y documentación."""
import json
import re
from pathlib import Path

import pytest

import bot_alertas as b

RAIZ = Path(__file__).resolve().parent.parent


def test_el_menu_de_telegram_es_valido():
    nombres = [c for c, _ in b.MENU]
    assert len(nombres) == len(set(nombres)) and len(nombres) <= 100
    assert all(re.fullmatch(r"[a-z0-9_]{1,32}", n) for n in nombres)          # reglas de Telegram para comandos
    assert all(1 <= len(d) <= 256 for _, d in b.MENU)


def test_la_ayuda_cabe_en_un_mensaje_y_menciona_cada_comando_del_menu():
    assert len(b.AYUDA) < 4096
    for comando, _ in b.MENU:
        if comando not in ("start", "ayuda"):
            assert f"/{comando}" in b.AYUDA, comando


def test_todos_los_comandos_del_menu_existen(cmd, monkeypatch):
    monkeypatch.setattr(b, "informe_diagnostico", lambda cfg, n=150: {"veredictos": {}, "detalle": [], "cffi": True})
    for comando, _ in b.MENU:
        respuesta = cmd(f"/{comando}")
        assert respuesta and "No conozco" not in respuesta, comando


def test_el_readme_documenta_cada_comando():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    faltan = [c for c, _ in b.MENU if c not in ("start", "ayuda") and f"/{c}" not in readme]
    assert not faltan, f"comandos sin documentar en el README: {faltan}"


def test_el_readme_documenta_cada_clave_de_la_configuracion():
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    claves = set(b.CONFIG_DEFECTO) - {"busquedas"}
    claves |= {"keyword", "min_precio", "max_precio", "excluir", "todas_las_palabras", "alias",
               "chollo_porcentaje", "paginas_maximas", "max_avisos"}
    faltan = sorted(c for c in claves if f"`{c}`" not in readme)
    assert not faltan, f"claves sin documentar en el README: {faltan}"


def test_los_ejemplos_de_configuracion_son_validos(tmp_path):
    for nombre in ("config.ejemplo.json",):
        datos = json.loads((RAIZ / nombre).read_text(encoding="utf-8"))
        (tmp_path / "config.json").write_text(json.dumps(datos))
        cfg = b.cargar_config()
        assert cfg["busquedas"] == datos["busquedas"] and set(datos) <= set(b.CONFIG_DEFECTO)


def test_el_env_de_ejemplo_solo_tiene_marcadores():
    texto = (RAIZ / ".env.ejemplo").read_text(encoding="utf-8")
    assert "pon_aqui" in texto and not re.search(r"\d{6,}:[A-Za-z0-9_-]{30,}", texto)   # ningún token real


def test_las_dependencias_estan_fijadas_a_una_version_exacta():
    for archivo in ("requirements.txt", "requirements-dev.txt"):
        for linea in (RAIZ / archivo).read_text().splitlines():
            if linea.strip() and not linea.startswith(("-r", "#")):
                assert re.fullmatch(r"[A-Za-z0-9_.-]+==\d+(\.\d+)*", linea.strip()), f"{archivo}: {linea}"


def test_el_gitignore_protege_los_secretos_y_los_datos():
    lineas = (RAIZ / ".gitignore").read_text().splitlines()
    for imprescindible in (".env", "data/", "datos.db", "config.json"):
        assert imprescindible in lineas, imprescindible


def test_el_ci_prueba_el_minimo_de_python_y_la_version_de_la_imagen_de_docker():
    version_docker = re.search(r"FROM python:(\d+\.\d+)", (RAIZ / "Dockerfile").read_text()).group(1)
    flujo = (RAIZ / ".github" / "workflows" / "tests.yml").read_text()
    versiones = re.findall(r'"(\d+\.\d+)"', re.search(r"python-version:\s*\[(.*?)\]", flujo).group(1))
    assert version_docker in versiones, "el CI no prueba la versión de Python de la imagen de Docker"
    assert "3.10" in versiones and "Python 3.10" in (RAIZ / "README.md").read_text()   # el mínimo anunciado se prueba


def test_dependabot_cubre_pip_acciones_y_docker():
    texto = (RAIZ / ".github" / "dependabot.yml").read_text()
    for ecosistema in ("pip", "github-actions", "docker"):
        assert f"package-ecosystem: {ecosistema}" in texto
