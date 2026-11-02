#!/usr/bin/env python3
"""
Bot de alertas: vigila Wallapop y Vinted por palabras clave y te avisa por Telegram.

Instalación:  pip install requests curl_cffi
Uso:
    python bot_alertas.py                  # arranca el bot
    python bot_alertas.py --diagnostico    # prueba las peticiones (no manda avisos)

Las búsquedas y ajustes viven en config.json (se crea solo la primera vez).
También puedes gestionarlas desde Telegram con comandos (/ayuda).
El bot lo relee en cada ciclo: puedes editarlo SIN reiniciar nada.
Credenciales: TELEGRAM_TOKEN y TELEGRAM_CHAT_ID (variables de entorno o archivo .env).

NOTA: Wallapop y Vinted no tienen API pública; se usan sus endpoints internos,
que cambian a menudo. Por eso el bot te avisa si una plataforma deja de funcionar.
"""

import html
import json
import os
import random
import re
import sqlite3
import statistics
import sys
import threading
import time
import unicodedata
import uuid
from pathlib import Path

import requests

try:
    from curl_cffi import requests as cffi_requests
    HAY_CFFI = True
except ImportError:
    HAY_CFFI = False

# Cargar .env automáticamente (si existe junto al script)
_env = Path(__file__).with_name(".env")
if _env.exists():
    for _l in _env.read_text().splitlines():
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ----------------------------- CONFIGURACIÓN -----------------------------

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

ARCHIVO_CONFIG = Path("config.json")
ARCHIVO_VISTOS = Path("vistos.json")   # formato antiguo; se migra a datos.db
ARCHIVO_DB = Path("datos.db")

VINTED_DOMINIO = "https://www.vinted.es"
WALLAPOP_ENDPOINTS = [
    "https://api.wallapop.com/api/v3/search",
    "https://api.wallapop.com/api/v3/general/search",
]
VINTED_ENDPOINTS = [
    "https://api.vinted.es/svc-catalogue/items",
    f"{VINTED_DOMINIO}/api/v2/catalog/items",   # antiguo, por si acaso
]

CONFIG_DEFECTO = {
    "intervalo_segundos": 180,
    "paginas_maximas": 3,
    "max_avisos_por_busqueda": 8,
    "plataformas": ["wallapop", "vinted"],
    "ubicacion": {"latitud": 43.263, "longitud": -2.935},
    "alertas_salud": {"fallos_seguidos": 3, "latido_diario": True, "hora_latido": 9},
    "horario_silencioso": {"activo": False, "desde": 1, "hasta": 8},
    "bajadas_precio": {"activo": True, "min_porcentaje": 10, "min_euros": 2},
    "chollos": {"activo": True, "porcentaje": 30, "min_muestras": 15, "dias": 60},
    "busquedas": [
        {"keyword": "minish cap", "min_precio": None, "max_precio": 30,
         "excluir": [], "todas_las_palabras": False},
        {"keyword": "zelda oracle of", "min_precio": None, "max_precio": 150,
         "excluir": [], "todas_las_palabras": False},
        {"keyword": "game cube", "min_precio": None, "max_precio": 30,
         "excluir": [], "todas_las_palabras": False},
    ],
}


def cargar_config(anterior: dict | None = None) -> dict:
    """Lee config.json. Si falta, lo crea; si está mal escrito, conserva la versión anterior."""
    if not ARCHIVO_CONFIG.exists():
        ARCHIVO_CONFIG.write_text(
            json.dumps(CONFIG_DEFECTO, indent=2, ensure_ascii=False), encoding="utf-8")
        print("[config] creado config.json de ejemplo")
        return json.loads(json.dumps(CONFIG_DEFECTO))
    try:
        cfg = json.loads(ARCHIVO_CONFIG.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("el archivo debe contener un objeto JSON { ... }")
    except (ValueError, OSError) as e:
        print(f"[config] config.json no es válido ({e}); sigo con la versión anterior")
        return anterior or json.loads(json.dumps(CONFIG_DEFECTO))
    res = json.loads(json.dumps(CONFIG_DEFECTO))
    for k in ("intervalo_segundos", "paginas_maximas", "max_avisos_por_busqueda", "plataformas", "busquedas"):
        if k in cfg:
            res[k] = cfg[k]
    for k in ("ubicacion", "alertas_salud", "horario_silencioso", "bajadas_precio", "chollos"):
        seccion = cfg.get(k)
        res[k] = {**CONFIG_DEFECTO[k], **(seccion if isinstance(seccion, dict) else {})}
    if not isinstance(res["plataformas"], list):
        res["plataformas"] = CONFIG_DEFECTO["plataformas"]
    try:
        res["intervalo_segundos"] = int(float(res["intervalo_segundos"]))
    except (TypeError, ValueError):
        res["intervalo_segundos"] = CONFIG_DEFECTO["intervalo_segundos"]
    brutas = res["busquedas"] if isinstance(res["busquedas"], list) else []
    validas = [x for x in brutas if isinstance(x, dict) and isinstance(x.get("keyword"), str) and x["keyword"].strip()]
    if len(validas) != len(brutas) or not isinstance(res["busquedas"], list):
        print("[config] ignoro búsquedas no válidas (cada una debe ser un objeto con un 'keyword' de texto)")
    res["busquedas"] = validas
    try:
        res["paginas_maximas"] = max(1, min(5, int(res["paginas_maximas"])))
    except (TypeError, ValueError):
        res["paginas_maximas"] = CONFIG_DEFECTO["paginas_maximas"]
    try:
        res["max_avisos_por_busqueda"] = max(0, min(50, int(res["max_avisos_por_busqueda"])))
    except (TypeError, ValueError):
        res["max_avisos_por_busqueda"] = CONFIG_DEFECTO["max_avisos_por_busqueda"]
    return res


# ------------------------------ UTILIDADES -------------------------------

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def nueva_sesion():
    """Sesión que imita a Chrome si curl_cffi está instalado."""
    if HAY_CFFI:
        return cffi_requests.Session(impersonate="chrome")
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    return s


def a_float(x):
    if isinstance(x, dict):
        x = x.get("amount")
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def resumen(resp, n: int = 150) -> str:
    cuerpo = (resp.text or "")[:n].replace("\n", " ")
    return f"HTTP {resp.status_code}: {cuerpo}"


def norm(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar títulos."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# ------------------------------- TELEGRAM --------------------------------


def enviar_telegram(texto: str, foto: str | None = None, preview: bool = True) -> bool:
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    try:
        if foto:
            r = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": TELEGRAM_CHAT_ID, "photo": foto,
                      "caption": texto, "parse_mode": "HTML"},
                timeout=20,
            )
            if r.ok:
                return True
        r = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": texto,
                  "parse_mode": "HTML", "disable_web_page_preview": not preview},
            timeout=20,
        )
        return r.ok
    except requests.RequestException as e:
        print(f"[telegram] error: {e}")
        return False


# ------------------------------- WALLAPOP --------------------------------

_sesion_wallapop = None


def buscar_wallapop(keyword: str, max_precio: float | None, cfg: dict,
                    cursor: str | None = None) -> tuple[list[dict], str | None]:
    """Devuelve (anuncios de una página, cursor de la siguiente o None)."""
    global _sesion_wallapop
    if _sesion_wallapop is None:
        _sesion_wallapop = nueva_sesion()

    params = {
        "keywords": keyword, "order_by": "newest", "source": "search_box",
        "latitude": cfg["ubicacion"]["latitud"],
        "longitude": cfg["ubicacion"]["longitud"],
        "language": "es_ES",
    }
    if cursor:
        params["next_page"] = cursor
    headers = {
        "Accept": "application/json", "Accept-Language": "es-ES,es;q=0.9",
        "Origin": "https://es.wallapop.com", "Referer": "https://es.wallapop.com/",
        "X-DeviceOS": "0",
    }

    errores, datos, cuerpo = [], None, {}
    for url in WALLAPOP_ENDPOINTS:
        r = _sesion_wallapop.get(url, params=params, headers=headers, timeout=20)
        if r.status_code == 200:
            try:
                cuerpo = r.json()
                datos = cuerpo.get("data", {})
                break
            except ValueError:
                errores.append(f"{url} -> respuesta no JSON")
        else:
            errores.append(f"{url} -> {resumen(r)}")
    if datos is None:
        raise RuntimeError(" | ".join(errores))

    items = (
        (datos.get("section") or {}).get("payload", {}).get("items")
        or datos.get("search_objects") or datos.get("items") or []
    )
    resultado = []
    for it in items:
        imagenes = it.get("images") or []
        foto = None
        if imagenes and isinstance(imagenes[0], dict):
            urls = imagenes[0].get("urls") or {}
            foto = urls.get("medium") or urls.get("big") or imagenes[0].get("medium")
        slug = it.get("web_slug") or it.get("id")
        resultado.append({
            "id": f"wallapop:{it.get('id')}",
            "titulo": it.get("title", "(sin título)"),
            "precio": a_float(it.get("price")),
            "url": f"https://es.wallapop.com/item/{slug}",
            "foto": foto,
            "plataforma": "Wallapop",
        })
    siguiente = (cuerpo.get("meta") or {}).get("next_page")
    return resultado, siguiente


# -------------------------------- VINTED ---------------------------------

_sesion_vinted = None


def _sesion_vinted_nueva():
    """Vinted exige cookies de sesión (access_token_web): se obtienen visitando la home."""
    s = nueva_sesion()
    r = s.get(VINTED_DOMINIO, headers={"Accept-Language": "es-ES,es;q=0.9"}, timeout=20)
    if r.status_code != 200:
        print(f"[vinted] aviso: la home respondió {resumen(r)}")
    return s


def _params_vinted(keyword: str, max_precio: float | None, pagina: int = 1) -> dict:
    params = {
        "search_text": keyword, "order": "newest_first", "page": pagina, "per_page": 30,
        "time": int(time.time()), "currency": "EUR",
        "global_search_session_id": str(uuid.uuid4()),
    }
    if max_precio:
        params["price_to"] = int(max_precio)
    return params


def _headers_vinted() -> dict:
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "es-ES,es;q=0.9",
        "Origin": VINTED_DOMINIO, "Referer": VINTED_DOMINIO + "/",
    }


def buscar_vinted(keyword: str, max_precio: float | None, cfg: dict,
                  cursor: int | None = None) -> tuple[list[dict], int | None]:
    """Devuelve (anuncios de una página, nº de la siguiente página o None)."""
    global _sesion_vinted
    cuerpo, errores = None, []

    for _ in range(2):   # si falla, renovar sesión y reintentar una vez
        if _sesion_vinted is None:
            _sesion_vinted = _sesion_vinted_nueva()
        errores = []
        for url in VINTED_ENDPOINTS:
            r = _sesion_vinted.get(url, params=_params_vinted(keyword, max_precio, cursor or 1),
                                   headers=_headers_vinted(), timeout=20)
            if r.status_code == 200:
                try:
                    cuerpo = r.json()
                    break
                except ValueError:
                    errores.append(f"{url} -> respuesta no JSON")
            else:
                errores.append(f"{url} -> {resumen(r)}")
        if cuerpo is not None:
            break
        _sesion_vinted = None
        time.sleep(2)
    if cuerpo is None:
        raise RuntimeError(" | ".join(errores))

    items = cuerpo.get("items") or (cuerpo.get("data") or {}).get("items") or []
    resultado = []
    for it in items:
        enlace = it.get("url") or it.get("path") or f"/items/{it.get('id')}"
        if enlace.startswith("/"):
            enlace = VINTED_DOMINIO + enlace
        resultado.append({
            "id": f"vinted:{it.get('id')}",
            "titulo": it.get("title", "(sin título)"),
            "precio": a_float(it.get("price")),
            "url": enlace,
            "foto": (it.get("photo") or {}).get("url"),
            "plataforma": "Vinted",
        })
    return resultado, ((cursor or 1) + 1 if resultado else None)


BUSCADORES = {"wallapop": buscar_wallapop, "vinted": buscar_vinted}

# ------------------------------- FILTROS ---------------------------------


def terminos_busqueda(b: dict) -> list[str]:
    """La palabra clave y sus variantes (alias), sin repetidos ni vacíos."""
    kw = b["keyword"]
    vistos, res = {norm(kw)}, [kw]
    for a in b.get("alias") or []:
        if isinstance(a, str) and a.strip() and norm(a) not in vistos:
            vistos.add(norm(a))
            res.append(a.strip())
    return res


def pasa_filtros(item: dict, b: dict, con_precio: bool = True) -> bool:
    """Aplica precio mínimo/máximo (opcional), palabras excluidas y 'todas las palabras'."""
    precio = item["precio"]
    if con_precio and precio is not None:
        if b.get("max_precio") is not None and precio > b["max_precio"]:
            return False
        if b.get("min_precio") is not None and precio < b["min_precio"]:
            return False
    titulo = norm(item["titulo"])
    if b.get("todas_las_palabras"):
        if not any(all(p in titulo for p in norm(t).split()) for t in terminos_busqueda(b)):
            return False
    for x in b.get("excluir") or []:
        if x.strip() and norm(x) in titulo:
            return False
    return True


# --------------------------------- SALUD ---------------------------------


def evaluar_salud(plataforma: str, ok: int, fallo: int, ultimo_error: str,
                  cfg: dict, estado: dict) -> None:
    """Cuenta ciclos seguidos en los que TODAS las búsquedas de una plataforma fallan."""
    s = estado["salud"].setdefault(plataforma, {"fallos": 0, "alertado": False})
    umbral = cfg["alertas_salud"]["fallos_seguidos"]
    if fallo and not ok:
        s["fallos"] += 1
        # espera creciente antes de volver a intentarlo (máx. 30 min)
        espera = min(cfg["intervalo_segundos"] * s["fallos"], 1800)
        estado["pausa_hasta"][plataforma] = time.time() + espera
        if s["fallos"] >= umbral and not s["alertado"]:
            enviar_telegram(
                f"⚠️ <b>{plataforma}</b> lleva {s['fallos']} ciclos seguidos fallando.\n"
                f"Último error: {html.escape(ultimo_error[:300])}\n\nMás detalle: /diagnostico")
            s["alertado"] = True
    elif ok:
        if s["alertado"]:
            enviar_telegram(f"✅ <b>{plataforma}</b> vuelve a funcionar")
        s["fallos"], s["alertado"] = 0, False
        estado["pausa_hasta"].pop(plataforma, None)


def latido(cfg: dict, estado: dict) -> None:
    """Mensaje diario de 'sigo vivo' con un resumen."""
    h = cfg["alertas_salud"]
    if not h.get("latido_diario"):
        return
    ahora = time.localtime()
    hoy = time.strftime("%Y-%m-%d", ahora)
    if ahora.tm_hour >= h["hora_latido"] and estado["ultimo_latido"] != hoy:
        lineas = []
        for p in cfg["plataformas"]:
            fallos = estado["salud"].get(p, {}).get("fallos", 0)
            lineas.append(f"• {p}: {'⚠️ fallando' if fallos else 'OK'}")
        enviar_telegram(
            f"💓 Sigo vivo. Desde el último resumen: {estado['avisos']} avisos "
            f"enviados en {estado['ciclos']} ciclos.\n" + "\n".join(lineas))
        estado.update(ultimo_latido=hoy, avisos=0, ciclos=0)


# --------------------------------- LÓGICA --------------------------------


def formatear(item: dict, keyword: str, nota: str | None = None) -> str:
    precio = f"{item['precio']:g} €" if item["precio"] is not None else "s/p"
    cuerpo = (
        f"🔔 <b>{item['plataforma']}</b> · «{html.escape(keyword)}»\n"
        f"{html.escape(item['titulo'])}\n"
        f"💶 {precio}\n"
    )
    if nota:
        cuerpo += f"{nota}\n"
    return cuerpo + item["url"]


# ------------------------- BASE DE DATOS (SQLite) -------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS anuncios (
    id TEXT NOT NULL, busqueda TEXT NOT NULL, plataforma TEXT,
    titulo TEXT, precio REAL, url TEXT, relevante INTEGER DEFAULT 1,
    primera REAL, ultima REAL,
    PRIMARY KEY (id, busqueda)
);
CREATE TABLE IF NOT EXISTS historial (id TEXT NOT NULL, busqueda TEXT NOT NULL, precio REAL, t REAL);
CREATE INDEX IF NOT EXISTS idx_historial ON historial (id, busqueda);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def abrir_db() -> sqlite3.Connection:
    db = sqlite3.connect(ARCHIVO_DB, timeout=10)
    db.executescript(SCHEMA)
    return db


def hay_marca(db, plataforma: str, kw: str) -> bool:
    """¿Ya se hizo la primera pasada (silenciosa) de esta búsqueda en esta plataforma?"""
    return db.execute("SELECT 1 FROM meta WHERE k=?", (f"init|{plataforma}|{kw}",)).fetchone() is not None


def poner_marca(db, plataforma: str, kw: str) -> None:
    db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, '1')", (f"init|{plataforma}|{kw}",))
    db.commit()


def migrar_vistos(db) -> None:
    """Importa el antiguo vistos.json (una sola vez) y lo renombra."""
    if not ARCHIVO_VISTOS.exists():
        return
    try:
        vistos = json.loads(ARCHIVO_VISTOS.read_text())
        if not isinstance(vistos, dict):
            raise ValueError("no es un objeto")
    except (OSError, ValueError) as e:
        print(f"[db] no pude leer vistos.json ({e}); lo ignoro")
        return
    ahora = time.time()
    for clave, ids in vistos.items():
        plataforma, _, kw = clave.partition("|")
        for i in ids:
            # precio y 'relevante' desconocidos: no cuentan para estadísticas
            db.execute("INSERT OR IGNORE INTO anuncios (id, busqueda, plataforma, relevante, primera, ultima) "
                       "VALUES (?, ?, ?, 0, ?, ?)", (i, kw, plataforma, ahora, ahora))
        db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, '1')", (f"init|{plataforma}|{kw}",))
    db.commit()
    ARCHIVO_VISTOS.replace(ARCHIVO_VISTOS.with_name("vistos.json.migrado"))
    print(f"[db] migrado vistos.json ({len(vistos)} búsquedas)")


def limpiar_db(db, dias: int = 120) -> None:
    """Borra anuncios que llevan mucho tiempo sin aparecer."""
    limite = time.time() - dias * 86400
    db.execute("DELETE FROM anuncios WHERE ultima < ?", (limite,))
    db.execute("DELETE FROM historial WHERE t < ?", (limite,))
    db.commit()


def estadisticas_precios(db, busqueda: str, dias: int) -> dict:
    """Mediana, mínimo y máximo de los anuncios relevantes vistos en los últimos `dias`."""
    desde = time.time() - dias * 86400
    precios = [r[0] for r in db.execute(
        "SELECT precio FROM anuncios WHERE busqueda=? AND relevante=1 AND precio>0 AND ultima>=?",
        (busqueda, desde))]
    if not precios:
        return {"n": 0, "mediana": None, "min": None, "max": None}
    return {"n": len(precios), "mediana": statistics.median(precios),
            "min": min(precios), "max": max(precios)}


def nota_chollo(it: dict, stats: dict | None, pct: float, ch: dict) -> str | None:
    if not stats or it["precio"] is None or stats["n"] < ch["min_muestras"]:
        return None
    mediana = stats["mediana"]
    if it["precio"] <= mediana * (1 - pct / 100):
        dto = (1 - it["precio"] / mediana) * 100
        return (f"🔥 <b>Posible chollo</b>: {dto:.0f}% por debajo de lo habitual "
                f"(mediana {mediana:g} € en {stats['n']} anuncios)")
    return None


def procesar_resultados(db, cfg: dict, b: dict, plataforma: str, items: list[dict],
                        primera_vez: bool) -> tuple[list[tuple[dict, str | None]], int]:
    """Actualiza la base de datos con lo encontrado y decide de qué avisar.

    Devuelve ([(anuncio, nota)], nº de anuncios nuevos). Se avisa de:
      · anuncios nuevos que pasan los filtros (con nota si parecen un chollo)
      · anuncios ya conocidos cuyo precio ha bajado de forma notable
    """
    kw = b["keyword"]
    ahora = time.time()
    ch, bj = cfg["chollos"], cfg["bajadas_precio"]
    pct = b.get("chollo_porcentaje", ch["porcentaje"])
    stats = None
    if not primera_vez and ch["activo"] and pct:
        stats = estadisticas_precios(db, kw, ch["dias"])   # antes de insertar lo nuevo

    avisar, nuevos = [], 0
    for it in items:
        precio = it["precio"]
        relevante = int(pasa_filtros(it, b, con_precio=False))
        fila = db.execute("SELECT precio FROM anuncios WHERE id=? AND busqueda=?",
                          (it["id"], kw)).fetchone()
        if fila is None:   # anuncio nuevo
            nuevos += 1
            db.execute("INSERT INTO anuncios (id, busqueda, plataforma, titulo, precio, url, relevante, "
                       "primera, ultima) VALUES (?,?,?,?,?,?,?,?,?)",
                       (it["id"], kw, plataforma, it["titulo"], precio, it["url"], relevante, ahora, ahora))
            if precio is not None:
                db.execute("INSERT INTO historial VALUES (?,?,?,?)", (it["id"], kw, precio, ahora))
            if not primera_vez and pasa_filtros(it, b):
                avisar.append((it, nota_chollo(it, stats, pct, ch) if ch["activo"] and pct else None))
            continue

        antes = fila[0]
        db.execute("UPDATE anuncios SET ultima=?, titulo=?, url=?, relevante=? WHERE id=? AND busqueda=?",
                   (ahora, it["titulo"], it["url"], relevante, it["id"], kw))
        if precio is not None and (antes is None or abs(antes - precio) > 0.005):
            db.execute("UPDATE anuncios SET precio=? WHERE id=? AND busqueda=?", (precio, it["id"], kw))
            db.execute("INSERT INTO historial VALUES (?,?,?,?)", (it["id"], kw, precio, ahora))
            if antes is not None and precio < antes and bj["activo"] and not primera_vez:
                caida, caida_pct = antes - precio, (antes - precio) / antes * 100
                if (caida >= bj["min_euros"] and caida_pct >= bj["min_porcentaje"]
                        and pasa_filtros(it, b)):
                    avisar.append((it, f"📉 <b>Bajada de precio</b>: {antes:g} € → {precio:g} € "
                                       f"(−{caida_pct:.0f}%)"))
    db.commit()
    return avisar, nuevos


def conocido(db, id_: str, kw: str) -> bool:
    return db.execute("SELECT 1 FROM anuncios WHERE id=? AND busqueda=?", (id_, kw)).fetchone() is not None


def obtener_resultados(plataforma: str, b: dict, cfg: dict, db, primera_vez: bool,
                       termino: str | None = None) -> tuple[list[dict], int]:
    """Descarga la primera página y solo pide más si TODA la anterior es desconocida.

    Si en la primera página ya hay anuncios que conocemos, no se ha escapado nada y no se pide más.
    Si todos son nuevos, puede haber más sin ver y se sigue, hasta `paginas_maximas`.
    En la primera pasada de una búsqueda solo se descarga una página (basta con memorizar).
    """
    buscador = BUSCADORES[plataforma]
    kw = b["keyword"]               # identidad de la búsqueda (memoria y estadísticas)
    consulta = termino or kw        # lo que se escribe en el buscador (clave o variante)
    try:
        limite = int(b.get("paginas_maximas", cfg["paginas_maximas"]))
    except (TypeError, ValueError):
        limite = cfg["paginas_maximas"]
    limite = 1 if primera_vez else max(1, min(5, limite))

    items: list[dict] = []
    cursor, paginas = None, 0
    while True:
        try:
            res = buscador(consulta, b.get("max_precio"), cfg) if cursor is None \
                else buscador(consulta, b.get("max_precio"), cfg, cursor)
        except Exception as e:
            if paginas == 0:
                raise                      # la primera página sí cuenta como fallo de la búsqueda
            print(f"[{plataforma}] '{kw}': falló la página {paginas + 1} ({e}); sigo con lo obtenido")
            break
        pagina, cursor = res if isinstance(res, tuple) else (res, None)
        paginas += 1
        vistos_ids = {i["id"] for i in items}
        nuevos_pagina = [i for i in pagina if i["id"] not in vistos_ids]   # por si repite resultados
        items += nuevos_pagina
        if paginas >= limite or cursor is None or not nuevos_pagina:
            break
        if any(conocido(db, i["id"], kw) for i in nuevos_pagina):
            break                          # ya hemos llegado a anuncios conocidos
        time.sleep(2)
    return items, paginas


def _prioridad(par: tuple) -> int:
    """Chollos primero, luego bajadas de precio, luego el resto (en orden de aparición)."""
    nota = par[1] or ""
    return 0 if nota.startswith("🔥") else 1 if nota.startswith("📉") else 2


def limite_avisos(cfg: dict, b: dict) -> int:
    """Máximo de avisos individuales por búsqueda y ciclo (0 = sin límite)."""
    try:
        v = int(b.get("max_avisos", cfg["max_avisos_por_busqueda"]))
    except (TypeError, ValueError):
        v = cfg["max_avisos_por_busqueda"]
    return max(0, min(50, v))


def resumen_extras(kw: str, extras: list[tuple], limite: int) -> str:
    """Un solo mensaje con los anuncios que no entraron en el límite."""
    cabecera = (f"📦 <b>+{len(extras)} anuncios más</b> de «{html.escape(kw)}» "
                f"(límite: {limite} avisos por ciclo)\n")
    cuerpo, usados = "", 0
    for it, nota in extras[:25]:
        precio = f"{it['precio']:g} €" if it["precio"] is not None else "s/p"
        marca = "🔥 " if (nota or "").startswith("🔥") else "📉 " if (nota or "").startswith("📉") else ""
        linea = f"\n{marca}<b>{it['plataforma']}</b> · {html.escape(it['titulo'][:100])} · {precio}\n{it['url']}\n"
        if len(cabecera) + len(cuerpo) + len(linea) > 3500:
            break
        cuerpo += linea
        usados += 1
    if usados < len(extras):
        cuerpo += (f"\n… y {len(extras) - usados} más. Afina la búsqueda (/excluir, /maximo) "
                   "o sube el límite con /limite.")
    return cabecera + cuerpo


def despachar_avisos(cfg: dict, estado: dict, b: dict, kw: str, avisar: list[tuple]) -> None:
    """Envía los avisos de una búsqueda aplicando el límite por ciclo.

    Los `limite` más interesantes (chollos, bajadas, luego los más recientes) van sueltos; el resto,
    en un mensaje resumen que se manda antes, para que lo mejor quede al final del chat.
    Durante una pausa u horario silencioso todo se guarda para el resumen posterior (sin límite aquí).
    """
    if not avisar:
        return
    if silenciado(cfg, estado):
        with LOCK:
            pend = estado.setdefault("pendientes", [])
            for it, nota in reversed(avisar):
                pend.append({"plataforma": it["plataforma"], "titulo": it["titulo"],
                             "precio": it["precio"], "url": it["url"], "kw": kw, "nota": nota})
            del pend[:-MAX_PENDIENTES]
            guardar_estado_persistente(estado)
        return

    limite = limite_avisos(cfg, b)
    ordenados = sorted(avisar, key=_prioridad)          # estable: dentro de cada grupo, lo más nuevo primero
    elegidos, resto = (ordenados[:limite], ordenados[limite:]) if limite else (ordenados, [])
    if resto and enviar_telegram(resumen_extras(kw, resto, limite), preview=False):
        estado["avisos"] += len(resto)
        time.sleep(1)
    for it, nota in reversed(elegidos):                 # lo mejor, lo último (abajo del todo en el chat)
        if enviar_telegram(formatear(it, kw, nota), it["foto"]):
            estado["avisos"] += 1
        time.sleep(1)


def ciclo(cfg: dict, db, estado: dict) -> None:
    enviar_pendientes_si_toca(cfg, estado)
    for plataforma in cfg["plataformas"]:
        if plataforma not in BUSCADORES:
            print(f"[config] plataforma desconocida: {plataforma}")
            continue
        restante = estado["pausa_hasta"].get(plataforma, 0) - time.time()
        if restante > 0:
            print(f"[{plataforma}] en pausa por fallos ({int(restante)} s)")
            continue

        ok = fallo = 0
        ultimo_error = ""
        for b in cfg["busquedas"]:
            kw = b["keyword"]
            estado["pulso"] = time.time()
            terminos = terminos_busqueda(b)
            items, avisar, nuevos, paginas, primera_vez = [], [], 0, 0, False
            exito, error_busqueda = False, None
            for termino in terminos:
                # cada variante tiene su propia "primera pasada silenciosa", pero comparte memoria
                clave_marca = kw if termino == kw else f"{kw}|alias:{termino}"
                primera_t = not hay_marca(db, plataforma, clave_marca)
                try:
                    items_t, pag_t = obtener_resultados(plataforma, b, cfg, db, primera_t, termino)
                except Exception as e:
                    error_busqueda = e
                    print(f"[{plataforma}] error buscando '{termino}': {e}")
                    continue
                exito = True
                av_t, nuevos_t = procesar_resultados(db, cfg, b, plataforma, items_t, primera_t)
                poner_marca(db, plataforma, clave_marca)
                items += items_t
                avisar += av_t
                nuevos += nuevos_t
                paginas += pag_t
                primera_vez = primera_vez or primera_t
                if len(terminos) > 1:
                    time.sleep(2)
            if not exito:            # solo cuenta como fallo si fallan TODOS los términos
                fallo += 1
                ultimo_error = str(error_busqueda)
                continue
            ok += 1
            items = list({i["id"]: i for i in items}.values())   # sin repetidos entre variantes

            despachar_avisos(cfg, estado, b, kw, avisar)

            print(f"[{plataforma}] '{kw}': {len(items)} resultados ({paginas} pág.), {nuevos} nuevos, "
                  f"{len(avisar)} avisos{' (memorizando)' if primera_vez else ''}")
            estado["pulso"] = time.time()
            time.sleep(2)   # pausa entre peticiones

        evaluar_salud(plataforma, ok, fallo, ultimo_error, cfg, estado)


# --------------------- SILENCIO, PENDIENTES Y COMANDOS ---------------------

LOCK = threading.RLock()
DESPERTAR = threading.Event()   # un comando puede adelantar el siguiente ciclo
ARCHIVO_ESTADO = Path("estado.json")
INDEFINIDO = 4102444800         # año 2100: "en pausa hasta que yo diga"
MAX_PENDIENTES = 200


def cargar_estado_persistente() -> dict:
    try:
        d = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("estado.json no es un objeto")
        return {"silencio_hasta": float(d.get("silencio_hasta", 0)),
                "pendientes": list(d.get("pendientes", []))}
    except (OSError, ValueError, TypeError):
        return {"silencio_hasta": 0.0, "pendientes": []}


def guardar_estado_persistente(estado: dict) -> None:
    with LOCK:
        datos = {"silencio_hasta": estado.get("silencio_hasta", 0),
                 "pendientes": estado.get("pendientes", [])}
        ARCHIVO_ESTADO.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


def silenciado(cfg: dict, estado: dict, ahora: float | None = None) -> bool:
    """True si hay una pausa activa (/pausa) o estamos en el horario silencioso."""
    ahora = time.time() if ahora is None else ahora
    if estado.get("silencio_hasta", 0) > ahora:
        return True
    hs = cfg.get("horario_silencioso") or {}
    if hs.get("activo"):
        h, d, a = time.localtime(ahora).tm_hour, hs.get("desde", 1), hs.get("hasta", 8)
        if d == a:
            return False
        return (d <= h < a) if d < a else (h >= d or h < a)
    return False


def enviar_pendientes_si_toca(cfg: dict, estado: dict) -> None:
    """Si ya no estamos en silencio, manda los anuncios acumulados en uno o varios mensajes."""
    with LOCK:
        if silenciado(cfg, estado) or not estado.get("pendientes"):
            return
        pendientes, estado["pendientes"] = estado["pendientes"], []
        guardar_estado_persistente(estado)
    n = len(pendientes)
    cabecera = f"📬 <b>{n} anuncio{'s' if n != 1 else ''} mientras no molestaba</b>\n"
    bloques, actual = [], cabecera
    for p in pendientes[-40:]:   # como mucho 40, los más recientes
        precio = f"{p['precio']:g} €" if p.get("precio") is not None else "s/p"
        nota = f"\n{p['nota']}" if p.get("nota") else ""
        linea = f"\n<b>{p['plataforma']}</b> · {html.escape(p['titulo'])} · {precio}{nota}\n{p['url']}\n"
        if len(actual) + len(linea) > 3500:
            bloques.append(actual)
            actual = ""
        actual += linea
    if n > 40:
        actual += f"\n… y {n - 40} más anteriores."
    bloques.append(actual)
    for bloque in bloques:
        enviar_telegram(bloque, preview=False)
        time.sleep(1)
    estado["avisos"] += n


# ------------------------------- COMANDOS --------------------------------


class ConfigError(Exception):
    pass


MAX_ALIAS = 3   # variantes por búsqueda: cada una es una petición más


AYUDA = (
    "🤖 <b>Comandos</b>\n"
    "/lista — ver tus búsquedas\n"
    "/anadir zelda minish cap 30 — nueva búsqueda (el último número es el precio máximo; "
    "si el nombre acaba en número y no quieres máximo, termina con «sin»)\n"
    "/quitar 2 — borrar la búsqueda nº 2\n"
    "/alias 3 gamecube — que la búsqueda 3 busque también «gamecube» y lo junte todo (/quitaralias 3 gamecube)\n"
    "/maximo 2 40 — precio máximo («sin» para quitarlo)\n"
    "/minimo 2 5 — precio mínimo («sin» para quitarlo)\n"
    "/excluir 2 funda — descartar anuncios con esa palabra o frase\n"
    "/permitir 2 funda — quitar una exclusión\n"
    "/pausa 3 — silenciar avisos 3 horas (sin número: hasta /reanudar)\n"
    "/reanudar — volver a recibir avisos\n"
    "/silencio 1-8 — sin avisos de 1:00 a 8:00 (/silencio off para quitarlo)\n"
    "/precios 2 — precio habitual (mediana, mín, máx) de la búsqueda 2\n"
    "/chollos 25 — marcar como chollo lo que esté ≥25% por debajo de la mediana (off / on; por búsqueda: /chollos 2 25)\n"
    "/bajadas 15 — avisar si un anuncio que ya conocía baja ≥15% (off / on)\n"
    "/paginas 3 — hasta cuántas páginas de resultados revisa por búsqueda (por búsqueda: /paginas 2 4)\n"
    "/limite 8 — máximo de avisos sueltos por búsqueda y ciclo; el resto, en un mensaje resumen (0 = sin límite; por búsqueda: /limite 2 5)\n"
    "/diagnostico — prueba Wallapop y Vinted y te dice qué falla\n"
    "/estado — resumen del bot\n\n"
    "Lo que salga durante una pausa o el horario silencioso se guarda y te llega junto al terminar."
)

MENU = [
    ("lista", "Ver mis búsquedas"), ("anadir", "Añadir búsqueda: /anadir palabras [precio]"),
    ("quitar", "Quitar una búsqueda por número"), ("alias", "Añadir una variante a una búsqueda"),
    ("quitaralias", "Quitar una variante"), ("maximo", "Fijar precio máximo"),
    ("minimo", "Fijar precio mínimo"), ("excluir", "Excluir una palabra de una búsqueda"),
    ("permitir", "Quitar una exclusión"), ("pausa", "Pausar avisos [horas]"),
    ("reanudar", "Reanudar avisos"), ("silencio", "Horario silencioso: /silencio 1-8"),
    ("precios", "Precio habitual de una búsqueda"), ("chollos", "Detector de chollos"),
    ("bajadas", "Avisos de bajada de precio"), ("paginas", "Páginas de resultados a revisar"), ("limite", "Máximo de avisos sueltos por ciclo"),
    ("diagnostico", "Probar las plataformas y ver qué falla"),
    ("estado", "Estado del bot"), ("ayuda", "Ayuda"),
]


def leer_raw() -> dict:
    """Lee config.json tal cual (sin rellenar valores). Si está roto, NO lo toca."""
    if not ARCHIVO_CONFIG.exists():
        return json.loads(json.dumps(CONFIG_DEFECTO))
    try:
        raw = json.loads(ARCHIVO_CONFIG.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("debe contener un objeto JSON { ... }")
        return raw
    except ValueError as e:
        raise ConfigError(f"config.json tiene un error de sintaxis ({html.escape(str(e))}). "
                          "Arréglalo en el archivo y vuelve a intentarlo.")


def guardar_raw(raw: dict) -> None:
    tmp = ARCHIVO_CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ARCHIVO_CONFIG)


def _indice(arg, total: int) -> int:
    try:
        i = int(arg)
    except (TypeError, ValueError):
        raise ConfigError("Indica el número de la búsqueda (mira /lista).")
    if not 1 <= i <= total:
        raise ConfigError(f"No existe la búsqueda {i}. Tienes {total} (mira /lista).")
    return i - 1


def _precio(arg):
    if arg is None:
        raise ConfigError("Falta el precio.")
    if norm(arg) in ("sin", "quitar", "no"):
        return None
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*€?", arg.strip())
    if not m:
        raise ConfigError(f"«{html.escape(arg)}» no es un precio válido.")
    v = float(m.group(1).replace(",", "."))
    return int(v) if v == int(v) else v


def _porcentaje(arg: str) -> int:
    m = re.fullmatch(r"(\d{1,2})\s*%?", arg.strip())
    if not m or not 1 <= int(m.group(1)) <= 95:
        raise ConfigError(f"«{html.escape(arg)}» no es un porcentaje válido (de 1 a 95).")
    return int(m.group(1))


def _texto_busqueda(i: int, b: dict) -> str:
    partes = []
    if b.get("max_precio") is not None:
        partes.append(f"máx {b['max_precio']:g} €")
    if b.get("min_precio") is not None:
        partes.append(f"mín {b['min_precio']:g} €")
    if b.get("alias"):
        partes.append("también: " + ", ".join(b["alias"]))
    if b.get("excluir"):
        partes.append("sin: " + ", ".join(b["excluir"]))
    if b.get("todas_las_palabras"):
        partes.append("todas las palabras")
    extra = f" — {' · '.join(partes)}" if partes else ""
    return f"{i}. <b>{html.escape(b['keyword'])}</b>{html.escape(extra)}"


def _comando(cmd: str, args: list[str], estado: dict) -> str:
    if cmd in ("start", "ayuda", "help"):
        return AYUDA

    if cmd == "lista":
        bs = leer_raw().get("busquedas", [])
        if not bs:
            return "No tienes búsquedas. Añade una con /anadir palabras [precio]."
        return "🔎 <b>Tus búsquedas</b>\n" + "\n".join(
            _texto_busqueda(i, b) for i, b in enumerate(bs, 1))

    if cmd == "anadir":
        if not args:
            raise ConfigError("Uso: /anadir palabras [precio máximo]")
        maximo = None
        if len(args) >= 2 and norm(args[-1]) == "sin":
            args = args[:-1]
        elif len(args) >= 2 and re.fullmatch(r"\d+(?:[.,]\d+)?\s*€?", args[-1]):
            maximo = _precio(args[-1])
            args = args[:-1]
        kw = " ".join(args).strip()[:80]
        raw = leer_raw()
        bs = raw.setdefault("busquedas", [])
        if any(norm(x.get("keyword", "")) == norm(kw) for x in bs):
            raise ConfigError(f"Ya vigilas «{html.escape(kw)}».")
        bs.append({"keyword": kw, "min_precio": None, "max_precio": maximo,
                   "excluir": [], "todas_las_palabras": False})
        guardar_raw(raw)
        DESPERTAR.set()
        tope = f" (máx {maximo:g} €)" if maximo is not None else ""
        palabras, consejo = kw.split(), ""
        if len(palabras) == 2:
            unida = html.escape("".join(palabras))
            consejo = f"\n💡 ¿Quieres que busque también «{unida}» todo junto? /alias {len(bs)} {unida}"
        return (f"✅ Añadida: <b>{html.escape(kw)}</b>{tope}\n"
                "La primera pasada solo memoriza lo que ya existe; te aviso de lo que salga a partir de ahora."
                + consejo)

    if cmd in ("quitar", "maximo", "minimo", "excluir", "permitir"):
        raw = leer_raw()
        bs = raw.get("busquedas", [])
        i = _indice(args[0] if args else None, len(bs))
        b = bs[i]
        nombre = html.escape(b["keyword"])
        if cmd == "quitar":
            bs.pop(i)
            guardar_raw(raw)
            return f"🗑️ Quitada: <b>{nombre}</b>"
        if cmd in ("maximo", "minimo"):
            if len(args) < 2:
                raise ConfigError(f"Uso: /{cmd} número precio")
            campo = "max_precio" if cmd == "maximo" else "min_precio"
            b[campo] = _precio(args[1])
            guardar_raw(raw)
            etiqueta = "máximo" if cmd == "maximo" else "mínimo"
            valor = f"{b[campo]:g} €" if b[campo] is not None else "sin límite"
            return f"✅ <b>{nombre}</b>: precio {etiqueta} {valor}"
        frase = " ".join(args[1:]).strip()
        if not frase:
            raise ConfigError(f"Uso: /{cmd} número palabra")
        lista = b.setdefault("excluir", [])
        if cmd == "excluir":
            if norm(frase) not in [norm(x) for x in lista]:
                lista.append(frase)
            guardar_raw(raw)
            return f"✅ <b>{nombre}</b>: descarto anuncios con «{html.escape(frase)}»"
        b["excluir"] = [x for x in lista if norm(x) != norm(frase)]
        guardar_raw(raw)
        return f"✅ <b>{nombre}</b>: ya no descarto «{html.escape(frase)}»"

    if cmd == "pausa":
        if args:
            try:
                horas = float(args[0].replace(",", "."))
            except ValueError:
                raise ConfigError("Uso: /pausa [horas], por ejemplo /pausa 3")
            if not 0 < horas <= 720:
                raise ConfigError("Indica entre 0 y 720 horas.")
            estado["silencio_hasta"] = time.time() + horas * 3600
            hasta = time.strftime("%H:%M del %d/%m", time.localtime(estado["silencio_hasta"]))
            msg = f"⏸️ Avisos en pausa hasta las {hasta}."
        else:
            estado["silencio_hasta"] = INDEFINIDO
            msg = "⏸️ Avisos en pausa hasta que escribas /reanudar."
        guardar_estado_persistente(estado)
        return msg + " Los anuncios nuevos se guardan y te los mando juntos al volver."

    if cmd == "reanudar":
        estado["silencio_hasta"] = 0
        guardar_estado_persistente(estado)
        if silenciado(cargar_config(), estado):
            return "▶️ Pausa quitada, pero sigues dentro del horario silencioso: te mando lo pendiente al terminar."
        DESPERTAR.set()   # el bucle principal envía lo acumulado en cuanto despierta
        return "▶️ Avisos reanudados." + (" Te mando lo acumulado en un momento." if estado.get("pendientes") else "")

    if cmd == "silencio":
        raw = leer_raw()
        hs = raw.setdefault("horario_silencioso", dict(CONFIG_DEFECTO["horario_silencioso"]))
        if not args:
            if hs.get("activo"):
                return f"🌙 Horario silencioso: de {hs['desde']}:00 a {hs['hasta']}:00. (/silencio off para quitarlo)"
            return "🔔 Sin horario silencioso. Actívalo con /silencio 1-8"
        if norm(args[0]) in ("off", "no", "quitar", "desactivar"):
            hs["activo"] = False
            guardar_raw(raw)
            return "🔔 Horario silencioso desactivado."
        m = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", "".join(args))
        if not m:
            raise ConfigError("Uso: /silencio 1-8 (de la 1:00 a las 8:00) o /silencio off")
        d, a = int(m[1]), int(m[2])
        if not (0 <= d <= 23 and 0 <= a <= 24) or d == a % 24:
            raise ConfigError("Horas no válidas: usa números de 0 a 23, distintos entre sí (ej. /silencio 1-8).")
        hs.update(activo=True, desde=d, hasta=a % 24)
        guardar_raw(raw)
        return f"🌙 Sin avisos de {d}:00 a {a % 24}:00. Lo que salga se te enviará junto al terminar."

    if cmd in ("chollos", "bajadas"):
        raw = leer_raw()
        if cmd == "bajadas":
            sec = raw.setdefault("bajadas_precio", dict(CONFIG_DEFECTO["bajadas_precio"]))
            if not args:
                c = {**CONFIG_DEFECTO["bajadas_precio"], **sec}
                return (f"📉 Bajadas de precio: {'activas' if c['activo'] else 'desactivadas'} · avisa si baja "
                        f"≥{c['min_porcentaje']}% y ≥{c['min_euros']} €.\n"
                        "Cambiar: /bajadas off · /bajadas on · /bajadas 15 (porcentaje mínimo)")
            v = norm(args[0])
            if v in ("off", "no"):
                sec["activo"] = False
                msg = "📉 Avisos de bajada de precio desactivados."
            elif v in ("on", "si"):
                sec["activo"] = True
                msg = "📉 Avisos de bajada de precio activados."
            else:
                sec["activo"], sec["min_porcentaje"] = True, _porcentaje(args[0])
                msg = f"📉 Avisaré cuando un anuncio baje al menos un {sec['min_porcentaje']}%."
            guardar_raw(raw)
            return msg

        sec = raw.setdefault("chollos", dict(CONFIG_DEFECTO["chollos"]))
        if not args:
            c = {**CONFIG_DEFECTO["chollos"], **sec}
            return (f"🔥 Chollos: {'activos' if c['activo'] else 'desactivados'} · marca un anuncio si está "
                    f"≥{c['porcentaje']}% por debajo de la mediana (mín. {c['min_muestras']} anuncios, "
                    f"últimos {c['dias']} días).\n"
                    "Global: /chollos off · /chollos on · /chollos 25\n"
                    "Por búsqueda: /chollos 2 25 · /chollos 2 off · /chollos 2 auto")
        if len(args) >= 2:   # por búsqueda
            bs = raw.get("busquedas", [])
            b = bs[_indice(args[0], len(bs))]
            v = norm(args[1])
            if v == "auto":
                b.pop("chollo_porcentaje", None)
                msg = f"🔥 <b>{html.escape(b['keyword'])}</b>: vuelve al ajuste global."
            elif v in ("off", "no"):
                b["chollo_porcentaje"] = 0
                msg = f"🔥 <b>{html.escape(b['keyword'])}</b>: sin detector de chollos."
            else:
                b["chollo_porcentaje"] = _porcentaje(args[1])
                msg = f"🔥 <b>{html.escape(b['keyword'])}</b>: chollo si está ≥{b['chollo_porcentaje']}% por debajo de la mediana."
        else:
            v = norm(args[0])
            if v in ("off", "no"):
                sec["activo"] = False
                msg = "🔥 Detector de chollos desactivado."
            elif v in ("on", "si"):
                sec["activo"] = True
                msg = "🔥 Detector de chollos activado."
            else:
                sec["activo"], sec["porcentaje"] = True, _porcentaje(args[0])
                msg = f"🔥 Chollo = al menos un {sec['porcentaje']}% por debajo de la mediana."
        guardar_raw(raw)
        return msg

    if cmd == "precios":
        bs = leer_raw().get("busquedas", [])
        b = bs[_indice(args[0] if args else None, len(bs))]
        cfg = cargar_config()
        db = abrir_db()
        try:
            st = estadisticas_precios(db, b["keyword"], cfg["chollos"]["dias"])
        finally:
            db.close()
        nombre = html.escape(b["keyword"])
        if st["n"] == 0:
            return f"📈 <b>{nombre}</b>: todavía no tengo precios guardados."
        pct = b.get("chollo_porcentaje", cfg["chollos"]["porcentaje"])
        txt = (f"📈 <b>{nombre}</b> (últimos {cfg['chollos']['dias']} días)\n"
               f"{st['n']} anuncios · mediana {st['mediana']:g} € · mín {st['min']:g} € · máx {st['max']:g} €")
        if cfg["chollos"]["activo"] and pct:
            txt += f"\nChollo: hasta {st['mediana'] * (1 - pct / 100):.2f} €".replace(".00 €", " €")
            if st["n"] < cfg["chollos"]["min_muestras"]:
                txt += f" (aún no se usa: hacen falta {cfg['chollos']['min_muestras']} anuncios)"
        return txt

    if cmd in ("alias", "quitaralias"):
        raw = leer_raw()
        bs = raw.get("busquedas", [])
        b = bs[_indice(args[0] if args else None, len(bs))]
        frase = " ".join(args[1:]).strip()[:80]
        if not frase:
            raise ConfigError(f"Uso: /{cmd} número palabra (por ejemplo /{cmd} 3 gamecube)")
        nombre = html.escape(b["keyword"])
        lista = list(b.get("alias") or [])
        if cmd == "alias":
            if norm(frase) == norm(b["keyword"]) or norm(frase) in [norm(x) for x in lista]:
                raise ConfigError(f"«{html.escape(frase)}» ya está en esa búsqueda.")
            if len(lista) >= MAX_ALIAS:
                raise ConfigError(f"Máximo {MAX_ALIAS} variantes por búsqueda (cada una es una petición más).")
            b["alias"] = lista + [frase]
            guardar_raw(raw)
            DESPERTAR.set()
            return (f"✅ <b>{nombre}</b>: ahora también busco «{html.escape(frase)}» y lo junto todo en una "
                    "sola búsqueda. La primera pasada de la variante solo memoriza lo que ya existe.")
        nueva = [x for x in lista if norm(x) != norm(frase)]
        if len(nueva) == len(lista):
            raise ConfigError(f"«{html.escape(frase)}» no es una variante de esa búsqueda.")
        if nueva:
            b["alias"] = nueva
        else:
            b.pop("alias", None)
        guardar_raw(raw)
        return f"✅ <b>{nombre}</b>: ya no busco «{html.escape(frase)}»."

    if cmd == "limite":
        raw = leer_raw()

        def _num(a: str) -> int:
            if not a.isdigit() or not 0 <= int(a) <= 50:
                raise ConfigError(f"«{html.escape(a)}» no vale: indica un número del 0 al 50 (0 = sin límite).")
            return int(a)

        if not args:
            n = cargar_config()["max_avisos_por_busqueda"]
            actual = f"{n} avisos" if n else "sin límite"
            return (f"📦 Límite: {actual} individuales por búsqueda y ciclo. Si hay más, los mejores llegan sueltos "
                    "(chollos y bajadas primero) y el resto en un solo mensaje resumen.\n"
                    "Cambiar: /limite 8 · /limite 0 (sin límite) · por búsqueda: /limite 2 5 · /limite 2 auto")
        if len(args) == 1:
            raw["max_avisos_por_busqueda"] = _num(args[0])
            guardar_raw(raw)
            n = raw["max_avisos_por_busqueda"]
            return f"📦 Límite de avisos por búsqueda y ciclo: {n if n else 'sin límite'}."
        bs = raw.get("busquedas", [])
        b = bs[_indice(args[0], len(bs))]
        if norm(args[1]) == "auto":
            b.pop("max_avisos", None)
            msg = f"📦 <b>{html.escape(b['keyword'])}</b>: vuelve al límite global."
        else:
            b["max_avisos"] = _num(args[1])
            msg = (f"📦 <b>{html.escape(b['keyword'])}</b>: "
                   f"{'hasta ' + str(b['max_avisos']) + ' avisos por ciclo' if b['max_avisos'] else 'sin límite'}.")
        guardar_raw(raw)
        return msg

    if cmd == "paginas":
        raw = leer_raw()

        def _num(a: str) -> int:
            if not a.isdigit() or not 1 <= int(a) <= 5:
                raise ConfigError(f"«{html.escape(a)}» no vale: indica un número del 1 al 5.")
            return int(a)

        if not args:
            n = cargar_config()["paginas_maximas"]
            return (f"📄 Páginas máximas por búsqueda: {n}. Solo pide más páginas si la anterior no tiene "
                    "ningún anuncio conocido.\nCambiar: /paginas 3 · por búsqueda: /paginas 2 4 · /paginas 2 auto")
        if len(args) == 1:
            raw["paginas_maximas"] = _num(args[0])
            guardar_raw(raw)
            return f"📄 Páginas máximas por búsqueda: {raw['paginas_maximas']}."
        bs = raw.get("busquedas", [])
        b = bs[_indice(args[0], len(bs))]
        if norm(args[1]) == "auto":
            b.pop("paginas_maximas", None)
            msg = f"📄 <b>{html.escape(b['keyword'])}</b>: vuelve al ajuste global."
        else:
            b["paginas_maximas"] = _num(args[1])
            msg = f"📄 <b>{html.escape(b['keyword'])}</b>: hasta {b['paginas_maximas']} páginas."
        guardar_raw(raw)
        return msg

    if cmd == "estado":
        cfg = cargar_config()
        lineas = ["📊 <b>Estado</b>",
                  f"🔎 {len(cfg['busquedas'])} búsquedas · cada ~{cfg['intervalo_segundos'] // 60} min"]
        for p in cfg["plataformas"]:
            f = estado.get("salud", {}).get(p, {}).get("fallos", 0)
            lineas.append(f"• {p}: {'⚠️ fallando (' + str(f) + ' ciclos)' if f else 'OK'}")
        if estado.get("silencio_hasta", 0) > time.time():
            lineas.append("⏸️ Avisos en pausa")
        hs = cfg.get("horario_silencioso") or {}
        if hs.get("activo"):
            lineas.append(f"🌙 Horario silencioso {hs['desde']}:00–{hs['hasta']}:00")
        if estado.get("pendientes"):
            lineas.append(f"📬 {len(estado['pendientes'])} anuncios guardados para enviar")
        lineas.append("🌐 Aviso externo de caída: " +
                      ("activado" if os.environ.get("HEALTHCHECK_URL", "").strip() else "no configurado"))
        return "\n".join(lineas)

    return "No conozco ese comando. Escribe /ayuda."


def ejecutar_comando(texto: str, estado: dict) -> str | None:
    """Interpreta un mensaje. Devuelve la respuesta, o None si no es un comando."""
    partes = (texto or "").strip().split()
    if not partes or not partes[0].startswith("/"):
        return None
    cmd = norm(partes[0][1:].split("@")[0])   # sin @NombreDelBot ni tildes (/añadir = /anadir)
    if cmd == "diagnostico":   # hace peticiones de red: no debe retener LOCK mientras tanto
        try:
            return _comando_diagnostico()
        except Exception as e:
            return f"⚠️ Error ejecutando el diagnóstico: {html.escape(str(e))}"
    try:
        with LOCK:
            return _comando(cmd, partes[1:], estado)
    except ConfigError as e:
        return f"⚠️ {e}"
    except Exception as e:
        return f"⚠️ Error ejecutando el comando: {html.escape(str(e))}"


def escuchar_comandos(estado: dict) -> None:
    """Hilo que atiende los comandos de Telegram. Solo obedece a TELEGRAM_CHAT_ID."""
    base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
    offset = None
    try:   # descartar mensajes antiguos para no repetir comandos tras un reinicio
        res = requests.get(f"{base}/getUpdates", params={"timeout": 0}, timeout=20).json().get("result", [])
        if res:
            offset = res[-1]["update_id"] + 1
        requests.post(f"{base}/setMyCommands", timeout=20, data={"commands": json.dumps(
            [{"command": c, "description": d} for c, d in MENU])})
    except Exception as e:
        print(f"[comandos] aviso al iniciar: {e}")

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": json.dumps(["message"])}
            if offset:
                params["offset"] = offset
            datos = requests.get(f"{base}/getUpdates", params=params, timeout=45).json()
            if not datos.get("ok"):
                print(f"[comandos] Telegram respondió: {str(datos)[:200]} "
                      "(¿hay otra copia del bot en marcha?)")
                time.sleep(10)
                continue
            for u in datos.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message") or {}
                if str((msg.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
                    continue   # ignora a cualquiera que no seas tú
                respuesta = ejecutar_comando(msg.get("text") or "", estado)
                if respuesta:
                    enviar_telegram(respuesta, preview=False)
        except Exception as e:
            print(f"[comandos] error: {e}")
            time.sleep(10)


# ------------------------------ VIGILANCIA -------------------------------

ARCHIVO_SALUD = Path("health.json")


def ping_externo(url: str) -> bool:
    """Avisa a un servicio externo (healthchecks.io) de que el bot sigue vivo."""
    try:
        return requests.get(url, timeout=10).ok
    except requests.RequestException as e:
        # solo el tipo de error: el mensaje completo incluiría la URL, que es secreta
        print(f"[ping] no se pudo avisar al servicio externo ({type(e).__name__})")
        return False


def vigilante(estado: dict, limite: float | None = None, pausa: float = 30, salir=os._exit,
              ping=None, cada: float | None = None) -> None:
    """Hilo que reinicia el bot si se queda atascado y escribe health.json para el healthcheck.

    `estado["pulso"]` se actualiza con cada avance (cada búsqueda, cada ciclo). Durante la espera entre
    ciclos apunta al futuro, así que esperar no cuenta como atasco. Si pasan más de `limite` segundos
    sin avance, el proceso termina con error y Docker (restart) o systemd lo vuelven a lanzar.
    Desactivar con WATCHDOG_SEGUNDOS=0.

    Si existe HEALTHCHECK_URL (healthchecks.io), hace ping cada `cada` segundos (por defecto 300)
    mientras el bot avanza con normalidad. Si los pings dejan de llegar, el servicio te avisa: cubre
    el caso de PC apagado o sin internet, cuando el bot no puede avisarte él mismo por Telegram.
    Antes de reiniciarse por un atasco envía /fail para que el aviso llegue enseguida.
    """
    if limite is None:
        try:
            limite = float(os.environ.get("WATCHDOG_SEGUNDOS", "600"))
        except ValueError:
            limite = 600.0
    ping = ping or ping_externo
    url = os.environ.get("HEALTHCHECK_URL", "").strip()
    if cada is None:
        try:
            cada = float(os.environ.get("HEALTHCHECK_CADA", "300"))
        except ValueError:
            cada = 300.0
    ultimo_ping = 0.0
    while not estado.get("_parar"):
        time.sleep(pausa)
        sin_avance = time.time() - estado.get("pulso", time.time())
        if limite > 0 and sin_avance > limite:
            print(f"[vigilante] sin avances desde hace {int(sin_avance)} s: reinicio el proceso")
            enviar_telegram(f"⚠️ El bot llevaba {int(sin_avance // 60)} min atascado. Lo reinicio.")
            if url:
                ping(url.rstrip("/") + "/fail")
            salir(1)
            return
        try:
            ARCHIVO_SALUD.write_text(json.dumps({"t": time.time()}))
        except OSError:
            pass
        if url and time.time() - ultimo_ping >= cada:
            # si el ping falla se reintenta al minuto, no a los 5
            ultimo_ping = time.time() if ping(url) else time.time() - cada + 60


_diag_lock = threading.Lock()   # solo un diagnóstico a la vez


def _veredicto(res: list[tuple]) -> str:
    """res: [(código HTTP o None, nº de anuncios o None)] de cada endpoint probado."""
    if any(c == 200 and n for c, n in res):
        return f"✅ funciona ({max(n for c, n in res if c == 200 and n)} resultados)"
    if any(c == 200 for c, _ in res):
        return "⚠️ responde pero sin resultados (¿ha cambiado el formato?)"
    codigos = [c for c, _ in res if c]
    if 403 in codigos:
        return "🚫 bloqueado o endpoint retirado (403)"
    if 429 in codigos:
        return "⏳ demasiadas peticiones (429): espera y no bajes el intervalo"
    if 404 in codigos:
        return "❓ ruta no encontrada (404): el endpoint ha cambiado"
    if not codigos:
        return "🔌 sin respuesta (conexión o tiempo agotado)"
    return f"❌ respuesta inesperada ({codigos[0]})"


def _linea_http(r, n: int) -> str:
    return f"HTTP {r.status_code}" if r.status_code == 200 else resumen(r, n)


def informe_diagnostico(cfg: dict, n: int = 150) -> dict:
    """Prueba cada endpoint de las plataformas configuradas.

    Devuelve {"veredictos": {plataforma: texto}, "detalle": [líneas], "cffi": bool}.
    `n` es cuánto del cuerpo de una respuesta de error se enseña.
    """
    kw = cfg["busquedas"][0]["keyword"] if cfg.get("busquedas") else "nintendo"
    plataformas = cfg.get("plataformas") or []
    detalle: list[str] = []
    veredictos: dict[str, str] = {}

    if "wallapop" in plataformas:
        detalle.append("== WALLAPOP ==")
        res = []
        s = nueva_sesion()
        params = {"keywords": kw, "order_by": "newest", "source": "search_box",
                  "latitude": cfg["ubicacion"]["latitud"], "longitude": cfg["ubicacion"]["longitud"],
                  "language": "es_ES"}
        cabeceras = {"Accept": "application/json", "X-DeviceOS": "0",
                     "Origin": "https://es.wallapop.com", "Referer": "https://es.wallapop.com/"}
        for url in WALLAPOP_ENDPOINTS:
            try:
                r = s.get(url, params=params, headers=cabeceras, timeout=20)
                nitems = None
                if r.status_code == 200:
                    try:
                        datos = r.json().get("data", {})
                        nitems = len((datos.get("section") or {}).get("payload", {}).get("items")
                                     or datos.get("search_objects") or datos.get("items") or [])
                    except ValueError:
                        nitems = 0
                res.append((r.status_code, nitems))
                detalle.append(f"{url}\n   {_linea_http(r, n)}" +
                               (f" · anuncios: {nitems}" if nitems is not None else ""))
            except Exception as e:
                res.append((None, None))
                detalle.append(f"{url}\n   EXCEPCIÓN {type(e).__name__}: {str(e)[:n]}")
        veredictos["Wallapop"] = _veredicto(res)

    if "vinted" in plataformas:
        detalle += ["", "== VINTED =="] if detalle else ["== VINTED =="]
        res = []
        try:
            s = nueva_sesion()
            r = s.get(VINTED_DOMINIO, headers={"Accept-Language": "es-ES,es;q=0.9"}, timeout=20)
            detalle.append(f"home: {_linea_http(r, n)}")
            try:
                detalle.append(f"cookies recibidas: {list(s.cookies.keys())}")   # solo los nombres
            except Exception:
                pass
            for url in VINTED_ENDPOINTS:
                try:
                    r = s.get(url, params=_params_vinted(kw, None), headers=_headers_vinted(), timeout=20)
                    nitems, extra = None, ""
                    if r.status_code == 200:
                        try:
                            j = r.json()
                            its = j.get("items") or (j.get("data") or {}).get("items") or []
                            nitems = len(its)
                            extra = f"\n   claves JSON: {list(j.keys())}"
                            if its:
                                extra += f"\n   campos de un anuncio: {list(its[0].keys())}"
                        except ValueError:
                            nitems, extra = 0, "\n   (la respuesta no es JSON)"
                    res.append((r.status_code, nitems))
                    detalle.append(f"{url}\n   {_linea_http(r, n)}" +
                                   (f" · anuncios: {nitems}" if nitems is not None else "") + extra)
                except Exception as e:
                    res.append((None, None))
                    detalle.append(f"{url}\n   EXCEPCIÓN {type(e).__name__}: {str(e)[:n]}")
        except Exception as e:
            res.append((None, None))
            detalle.append(f"EXCEPCIÓN {type(e).__name__}: {str(e)[:n]}")
        veredictos["Vinted"] = _veredicto(res)

    return {"veredictos": veredictos, "detalle": detalle, "cffi": HAY_CFFI}


def diagnostico() -> None:
    """Versión para terminal: python bot_alertas.py --diagnostico"""
    inf = informe_diagnostico(cargar_config())
    print(f"curl_cffi instalado: {inf['cffi']}\n")
    for plataforma, texto in inf["veredictos"].items():
        print(f"{plataforma}: {texto}")
    print("\n" + "\n".join(inf["detalle"]))


def _recortar_html(texto: str, limite: int) -> str:
    """Escapa para HTML de Telegram y recorta el texto ANTES de escapar (para no partir una entidad)."""
    while len(html.escape(texto)) > limite and texto:
        texto = texto[: int(len(texto) * 0.8)]
    return html.escape(texto)


def _comando_diagnostico() -> str:
    if not _diag_lock.acquire(blocking=False):
        return "⏳ Ya hay un diagnóstico en marcha. Espera a que termine."
    try:
        enviar_telegram("🔍 Ejecutando diagnóstico… puede tardar hasta un minuto.", preview=False)
        inf = informe_diagnostico(cargar_config(), n=80)
        lineas = ["🔍 <b>Diagnóstico</b>"]
        lineas += [f"{p}: {html.escape(v)}" for p, v in inf["veredictos"].items()]
        lineas.append("curl_cffi: " + ("sí" if inf["cffi"] else "NO (sin él es probable que den 403/404)"))
        detalle = "\n".join(inf["detalle"])
        if len(detalle) > 1500:
            detalle = detalle[:1500] + "…"
        return "\n".join(lineas) + f"\n\n<pre>{_recortar_html(detalle, 2500)}</pre>"
    finally:
        _diag_lock.release()


def main() -> None:
    if "--diagnostico" in sys.argv:
        diagnostico()
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise SystemExit("Faltan TELEGRAM_TOKEN y/o TELEGRAM_CHAT_ID")
    if not HAY_CFFI:
        print("AVISO: curl_cffi no está instalado (pip install curl_cffi); "
              "con requests a secas es probable que den 403/404.")

    cfg = cargar_config()
    db = abrir_db()
    migrar_vistos(db)
    limpiar_db(db)
    estado = {"salud": {}, "pausa_hasta": {}, "avisos": 0, "ciclos": 0,
              "ultimo_latido": time.strftime("%Y-%m-%d"), **cargar_estado_persistente()}
    enviar_telegram("✅ Bot de alertas iniciado. Escribe /ayuda para ver los comandos.")
    estado["pulso"] = time.time()
    threading.Thread(target=escuchar_comandos, args=(estado,), daemon=True).start()
    if os.environ.get("HEALTHCHECK_URL", "").strip():
        print("[ping] aviso externo de caída activado")
    threading.Thread(target=vigilante, args=(estado,), daemon=True).start()

    while True:
        cfg = cargar_config(cfg)   # relee config.json: los cambios se aplican sin reiniciar
        try:
            ciclo(cfg, db, estado)
            estado["errores_seguidos"] = 0
        except Exception as e:   # un fallo inesperado no debe tumbar el bot
            estado["errores_seguidos"] = estado.get("errores_seguidos", 0) + 1
            print(f"[ciclo] error inesperado ({estado['errores_seguidos']} seguidos): {e!r}")
            if estado["errores_seguidos"] == 3:
                enviar_telegram("⚠️ El bot no consigue completar los ciclos por un error interno.\n"
                                f"Último error: {html.escape(repr(e)[:300])}\n\nRevisa los logs del contenedor.")
        estado["ciclos"] += 1
        if estado["ciclos"] % 480 == 0:   # más o menos una vez al día
            limpiar_db(db)
        latido(cfg, estado)
        # intervalo con algo de aleatoriedad; un comando (/anadir, /reanudar) puede despertarlo antes
        espera = max(60, cfg["intervalo_segundos"]) * random.uniform(0.85, 1.15)
        estado["pulso"] = time.time() + espera   # esperar no cuenta como atasco
        DESPERTAR.wait(timeout=espera)
        DESPERTAR.clear()
        estado["pulso"] = time.time()


if __name__ == "__main__":
    main()
