# Bot de alertas Wallapop + Vinted → Telegram

Vigila **Wallapop** y **Vinted** buscando las palabras clave que le indiques y te manda un aviso a **Telegram** cuando aparece un anuncio nuevo. Se gestiona desde el propio chat de Telegram y está pensado para dejarlo corriendo en una Raspberry Pi con Docker.

## Qué hace

- Avisa de anuncios nuevos con foto, precio y enlace.
- Filtros por búsqueda: precio mínimo y máximo, palabras excluidas y «todas las palabras».
- **Bajadas de precio** de anuncios que ya conocía.
- **Detector de chollos**: marca los anuncios muy por debajo del precio habitual (mediana) de la búsqueda.
- Gestión desde Telegram: añadir y quitar búsquedas, pausar avisos, horario silencioso…
- Alertas de salud (te avisa si una plataforma deja de funcionar), resumen diario y reinicio automático si se atasca.

> ⚠️ Wallapop y Vinted **no tienen API pública**. El bot usa sus endpoints internos, que pueden cambiar sin aviso, y sus condiciones de uso restringen el acceso automatizado. Úsalo solo de forma personal y con intervalos moderados (no bajes de 2–3 minutos). Sin garantías.

## Requisitos

- Una Raspberry Pi (Raspberry Pi OS **de 64 bits**) o cualquier Linux, con **Docker** y Docker Compose. Alternativa: Python 3.10 o superior.
- Una cuenta de Telegram.
- Mejor ejecutarlo desde una **conexión doméstica**: las IPs de servidores/VPS suelen estar bloqueadas, sobre todo por Vinted.

## 1. Crear el bot de Telegram

1. Habla con [@BotFather](https://t.me/BotFather), envía `/newbot` y sigue los pasos. Guarda el **token**.
2. Abre tu bot y pulsa **Iniciar** (o envíale cualquier mensaje).
3. Abre en el navegador `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y copia el número de `"chat":{"id": ...}`. Ese es tu **chat ID**.

## 2. Instalación con Docker (recomendada)

```bash
git clone https://github.com/<tu-usuario>/<tu-repositorio>.git
cd <tu-repositorio>

# Credenciales
cp .env.ejemplo .env
nano .env                      # pon TELEGRAM_TOKEN y TELEGRAM_CHAT_ID

# Configuración (búsquedas y ajustes)
mkdir -p data
cp config.ejemplo.json data/config.json
nano data/config.json          # tus búsquedas, tu ubicación…

# Arrancar
docker compose up -d --build
```

Deberías recibir «✅ Bot de alertas iniciado» en Telegram. Escríbele `/ayuda` para ver los comandos.

Con `restart: unless-stopped`, el bot arranca solo cuando se enciende la Raspberry.

## 3. Instalación sin Docker

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.ejemplo .env && nano .env
cp config.ejemplo.json config.json && nano config.json
./venv/bin/python bot_alertas.py
```

Lee `.env` y `config.json` de la carpeta desde la que lo lances.

## Configuración (`config.json`)

El bot relee el archivo en cada ciclo: puedes editarlo **sin reiniciar**. Si tiene un error de sintaxis, avisa por consola y sigue con la configuración anterior.

| Clave | Qué hace |
|---|---|
| `intervalo_segundos` | Tiempo entre revisiones (con ±15% de aleatoriedad). Mínimo efectivo: 60. |
| `paginas_maximas` | Páginas de resultados que pide como máximo (1–5). Solo pide la siguiente si la anterior no tiene ningún anuncio conocido. |
| `plataformas` | `"wallapop"`, `"vinted"` o ambas. |
| `ubicacion` | Latitud y longitud para Wallapop. |
| `alertas_salud` | Aviso tras `fallos_seguidos` ciclos fallando; resumen diario a `hora_latido`. |
| `horario_silencioso` | Sin avisos entre `desde` y `hasta` (lo acumulado llega junto al terminar). |
| `bajadas_precio` | Avisa si baja ≥ `min_porcentaje` y ≥ `min_euros`. |
| `chollos` | Marca como chollo lo que esté `porcentaje` % por debajo de la mediana (hacen falta `min_muestras` anuncios de los últimos `dias`). |
| `busquedas` | Lista de búsquedas (ver abajo). |

Cada elemento de `busquedas`:

| Clave | Qué hace |
|---|---|
| `keyword` | Texto a buscar. |
| `min_precio` / `max_precio` | Rango de precio en € (`null` = sin límite). |
| `excluir` | Palabras o frases que descartan un anuncio (sin distinguir mayúsculas ni tildes). |
| `todas_las_palabras` | Si es `true`, el título debe contener todas las palabras de `keyword`. |
| `chollo_porcentaje` | Opcional: umbral de chollo propio (`0` = desactivado). |
| `paginas_maximas` | Opcional: páginas máximas propias. |

La primera vez que se ve una búsqueda, el bot solo **memoriza** lo que ya existe y no avisa; te avisa de lo que aparezca después.

## Comandos de Telegram

El bot solo obedece al chat configurado en `TELEGRAM_CHAT_ID`.

| Comando | Qué hace |
|---|---|
| `/lista` | Ver las búsquedas numeradas |
| `/anadir zelda minish cap 30` | Nueva búsqueda (el último número es el precio máximo; termina con `sin` si no quieres máximo) |
| `/quitar 2` | Borra la búsqueda nº 2 |
| `/maximo 2 40` · `/minimo 2 5` | Precio máximo / mínimo (`sin` lo quita) |
| `/excluir 2 funda` · `/permitir 2 funda` | Añade o quita una exclusión |
| `/pausa 3` · `/reanudar` | Pausa los avisos 3 horas (sin número: hasta `/reanudar`) |
| `/silencio 1-8` · `/silencio off` | Horario sin avisos |
| `/precios 2` | Mediana, mínimo y máximo de la búsqueda 2 |
| `/chollos 25` · `/chollos 2 off` | Umbral de chollo, global o por búsqueda |
| `/bajadas 15` · `/bajadas off` | Umbral de bajada de precio |
| `/paginas 3` | Páginas máximas por búsqueda |
| `/estado` · `/ayuda` | Estado del bot y ayuda |

## Mantenimiento

```bash
docker compose logs -f         # ver lo que hace en directo
docker compose restart         # reiniciar
docker compose down            # parar
docker ps                      # debería mostrar (healthy)
```

**Actualizar:** `git pull && docker compose up -d --build`.

**Copia de seguridad:** guarda la carpeta `data/` (`config.json` y `datos.db`).

**Una sola copia a la vez:** Telegram no permite que dos programas atiendan los comandos del mismo bot. Si lo pruebas en tu PC, páralo antes de arrancarlo en la Raspberry.

## Solución de problemas

Para ver qué responde cada web sin enviar avisos:

```bash
docker compose run --rm bot-alertas python /app/bot_alertas.py --diagnostico
```

(sin Docker: `./venv/bin/python bot_alertas.py --diagnostico`)

- **403 o 404 constantes**: la web ha cambiado su API o te está bloqueando. Comprueba que no usas VPN, que `curl_cffi` está instalado y que la IP no es de un servidor.
- **`curl_cffi` no se instala**: usa un sistema operativo de 64 bits (`uname -m` debe dar `aarch64` o `x86_64`).
- **No llegan avisos tras arrancar**: es lo esperado en la primera pasada de cada búsqueda; solo avisa de lo que aparezca después.
- **Llegan avisos duplicados**: hay dos copias del bot en marcha.

## Limitaciones

- La mediana del detector de chollos solo tiene sentido en búsquedas concretas; en búsquedas amplias (que mezclan consolas, juegos y accesorios) puede dar falsos chollos.

## Licencia
