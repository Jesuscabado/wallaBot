# Bot de alertas Wallapop + Vinted → Telegram

![tests](https://github.com/<tu-usuario>/<tu-repositorio>/actions/workflows/tests.yml/badge.svg)

Vigila **Wallapop** y **Vinted** buscando las palabras clave que le indiques y te manda un aviso a **Telegram** cuando aparece un anuncio nuevo. Se gestiona desde el propio chat de Telegram y está pensado para dejarlo corriendo en una Raspberry Pi con Docker.

## Qué hace

- Avisa de anuncios nuevos con foto, precio y enlace.
- Filtros por búsqueda: precio mínimo y máximo, palabras excluidas y «todas las palabras».
- **Variantes** de una búsqueda («game cube» + «gamecube»), unidas en una sola, sin avisos duplicados.
- **Límite de avisos por ciclo**: si una búsqueda trae muchos anuncios nuevos de golpe, los mejores llegan sueltos y el resto en un solo mensaje resumen.
- **Bajadas de precio** de anuncios que ya conocía.
- **Detector de chollos**: marca los anuncios muy por debajo del precio habitual (mediana) de la búsqueda.
- Gestión desde Telegram: añadir y quitar búsquedas, pausar avisos, horario silencioso…
- Alertas de salud (te avisa si una plataforma deja de funcionar), resumen diario y reinicio automático si se atasca.
- Opcional: aviso externo si el bot **se apaga del todo** (PC apagado, sin internet…), con [healthchecks.io](https://healthchecks.io).

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
| `max_avisos_por_busqueda` | Máximo de avisos individuales por búsqueda y ciclo (0–50; `0` = sin límite). Por defecto, 8. |
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
| `alias` | Variantes que se buscan además de `keyword` y se unen en una sola búsqueda (máx. 3), por ejemplo `["gamecube"]`. |
| `excluir` | Palabras o frases que descartan un anuncio (sin distinguir mayúsculas ni tildes). |
| `todas_las_palabras` | Si es `true`, el título debe contener todas las palabras de `keyword` o de alguna de sus variantes. |
| `chollo_porcentaje` | Opcional: umbral de chollo propio (`0` = desactivado). |
| `paginas_maximas` | Opcional: páginas máximas propias. |
| `max_avisos` | Opcional: límite de avisos propio (`0` = sin límite). |

La primera vez que se ve una búsqueda, el bot solo **memoriza** lo que ya existe y no avisa; te avisa de lo que aparezca después. Lo mismo ocurre con cada variante nueva que añadas.

**Variantes (`alias`):** Wallapop y Vinted devuelven resultados distintos para «game cube» y «gamecube». Con una variante, el bot consulta las dos y las trata como **una sola búsqueda**: comparten memoria y estadísticas de precios, y un anuncio que salga por las dos vías te llega una vez. Cada variante supone una petición más por ciclo, así que el máximo es 3 por búsqueda y conviene usarlas solo donde compensen.

## Comandos de Telegram

El bot solo obedece al chat configurado en `TELEGRAM_CHAT_ID`.

| Comando | Qué hace |
|---|---|
| `/lista` | Ver las búsquedas numeradas |
| `/anadir zelda minish cap 30` | Nueva búsqueda (el último número es el precio máximo; termina con `sin` si no quieres máximo) |
| `/quitar 2` | Borra la búsqueda nº 2 |
| `/alias 3 gamecube` · `/quitaralias 3 gamecube` | Añade o quita una variante a la búsqueda 3 |
| `/maximo 2 40` · `/minimo 2 5` | Precio máximo / mínimo (`sin` lo quita) |
| `/excluir 2 funda` · `/permitir 2 funda` | Añade o quita una exclusión |
| `/pausa 3` · `/reanudar` | Pausa los avisos 3 horas (sin número: hasta `/reanudar`) |
| `/silencio 1-8` · `/silencio off` | Horario sin avisos |
| `/precios 2` | Mediana, mínimo y máximo de la búsqueda 2 |
| `/chollos 25` · `/chollos 2 off` | Umbral de chollo, global o por búsqueda |
| `/bajadas 15` · `/bajadas off` | Umbral de bajada de precio |
| `/paginas 3` | Páginas máximas por búsqueda |
| `/limite 8` · `/limite 2 5` · `/limite 0` | Máximo de avisos sueltos por ciclo, global o por búsqueda (`0` = sin límite) |
| `/diagnostico` | Prueba Wallapop y Vinted y te dice qué falla (ver más abajo) |
| `/estado` · `/ayuda` | Estado del bot y ayuda |

## Límite de avisos por ciclo

Una búsqueda amplia, o un cambio de filtros, puede traer decenas de anuncios nuevos de golpe. Para no llenarte el chat, el bot envía como máximo `max_avisos_por_busqueda` avisos **sueltos** por búsqueda y ciclo (8 por defecto). Si hay más:

- Van sueltos los más interesantes: primero los **chollos**, luego las **bajadas de precio** y después los más recientes.
- El resto llega agrupado en **un único mensaje resumen** («📦 +12 anuncios más de «zelda»…») con título, precio y enlace de cada uno (hasta 25; si hay más, te lo indica).
- El resumen se manda antes y los avisos sueltos después, de modo que lo mejor queda abajo del todo en el chat.

Los anuncios del resumen **cuentan como vistos**: no vuelven a avisar. Durante una pausa o el horario silencioso, el límite no se aplica; todo se guarda y llega junto al terminar (con su propio tope de 40). Si te llega a menudo el mensaje resumen, afina la búsqueda con `/excluir`, `/maximo` o `todas_las_palabras`, o sube el límite con `/limite`.

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

## Aviso externo de caída (opcional)

El bot te avisa por Telegram si una plataforma falla, pero si se apaga el equipo o se cae internet **no puede avisarte él mismo**. Para ese caso puedes usar un servicio externo gratuito, [healthchecks.io](https://healthchecks.io): el bot le manda un «ping» cada 5 minutos mientras funciona con normalidad, y si los pings dejan de llegar, el servicio te escribe.

1. Crea una cuenta en healthchecks.io y añade un **check** (por ejemplo, «Bot Wallapop»).
2. Configúralo con **Period: 10 minutes** y **Grace Time: 5 minutes**. Te avisará unos 15 minutos después de que el bot deje de dar señales.
3. Copia la **URL de ping** (con la forma `https://hc-ping.com/<uuid>`) y ponla en tu `.env`:
   ```
   HEALTHCHECK_URL=https://hc-ping.com/tu-uuid
   ```
4. Elige cómo quieres que te avise en **Integrations** (correo, Telegram, notificación push…).
5. Reinicia el bot: `docker compose up -d --force-recreate`. Con `/estado` verás «Aviso externo de caída: activado», y en el panel del servicio, el check pasará a verde tras el primer ping.

Detalles:

- Solo hace ping mientras el bot **avanza con normalidad**. Si se queda atascado, se reinicia solo y antes manda un aviso de fallo inmediato (`/fail`).
- La URL es secreta: quien la conozca puede enviar pings en tu nombre. No la subas al repositorio (`.env` ya está en el `.gitignore`); el bot tampoco la escribe en los logs.
- Cadencia opcional: `HEALTHCHECK_CADA=300` (segundos entre pings). Si la cambias, ajusta el *Period* del check en consecuencia.
- Está pensado para healthchecks.io. Otros servicios que aceptan un simple GET de «latido» (como los monitores *push* de Uptime Kuma) también sirven para los pings normales, pero el aviso inmediato `/fail` es específico de healthchecks.io.

## Solución de problemas

Lo más rápido es escribir **`/diagnostico`** a tu bot. Prueba las peticiones a cada plataforma configurada y responde con un veredicto y el detalle técnico, por ejemplo:

```
🔍 Diagnóstico
Wallapop: ✅ funciona (30 resultados)
Vinted: ❓ ruta no encontrada (404): el endpoint ha cambiado
curl_cffi: sí
```

| Veredicto | Qué suele significar |
|---|---|
| ✅ funciona | La plataforma responde con anuncios. Si aun así no te llega nada, el problema está en tus filtros o en la búsqueda. |
| ⚠️ responde pero sin resultados | Respuesta correcta, pero vacía o con otro formato. Prueba otra palabra clave; si persiste, ha cambiado el formato de la respuesta. |
| 🚫 bloqueado (403) | Bloqueo anti-bot o endpoint retirado. Comprueba que no usas VPN ni IP de servidor y que `curl_cffi` está instalado. |
| ❓ ruta no encontrada (404) | El endpoint ha cambiado. Hay que actualizar el código. |
| ⏳ demasiadas peticiones (429) | Te están limitando. No bajes el intervalo y espera un rato. |
| 🔌 sin respuesta | Sin conexión o tiempo agotado. |

Cuando una plataforma lleva varios ciclos fallando, el aviso que te manda el bot ya te remite a este comando. Si necesitas ayuda para arreglarlo, **copia el mensaje completo** del diagnóstico: no incluye tu token ni tu chat ID, solo códigos de respuesta y nombres de cookies.

Desde la terminal también puedes lanzarlo, sin enviar nada a Telegram:

```bash
docker compose run --rm bot-alertas python /app/bot_alertas.py --diagnostico
```

(sin Docker: `./venv/bin/python bot_alertas.py --diagnostico`)

- **403 o 404 constantes**: la web ha cambiado su API o te está bloqueando. Comprueba que no usas VPN, que `curl_cffi` está instalado y que la IP no es de un servidor.
- **`curl_cffi` no se instala**: usa un sistema operativo de 64 bits (`uname -m` debe dar `aarch64` o `x86_64`).
- **No llegan avisos tras arrancar**: es lo esperado en la primera pasada de cada búsqueda; solo avisa de lo que aparezca después.
- **Llegan avisos duplicados**: hay dos copias del bot en marcha.
- **healthchecks.io dice que el bot está caído y no lo está**: comprueba que el *Period* del check es mayor que `HEALTHCHECK_CADA` (300 s por defecto) y que la URL del `.env` es la correcta.

## Limitaciones

- La mediana del detector de chollos solo tiene sentido en búsquedas concretas; en búsquedas amplias (que mezclan consolas, juegos y accesorios) puede dar falsos chollos.

## Desarrollo y pruebas

El proyecto incluye una suite de pruebas con [pytest](https://pytest.org). No necesitas Telegram, Wallapop ni Vinted para ejecutarla: las pruebas simulan las respuestas y **bloquean el acceso a la red** (si una prueba intentara usarla, falla).

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m pytest
```

Cada prueba trabaja en una carpeta temporal vacía, con credenciales falsas y un reloj simulado, así que no toca tu `config.json`, tu `datos.db` ni tu `.env`. Para escribir otra, mira `tests/conftest.py`: ya hay *fixtures* para capturar lo que se enviaría a Telegram (`telegram`), simular un buscador (`web`), abrir una base de datos limpia (`db`), ejecutar un comando (`cmd`) y leer o escribir `config.json` (`cfg_json`).

**Integración continua.** GitHub Actions ejecuta las pruebas en cada `push` y `pull request` con Python 3.10 (el mínimo que admite el código) y 3.12 (la versión de la imagen de Docker), y comprueba además que la imagen se construye y lleva `curl_cffi` instalado.

**Dependencias fijadas.** `requirements.txt` fija versiones exactas, para que una actualización de `requests` o `curl_cffi` no rompa el bot sin avisar. [Dependabot](.github/dependabot.yml) propone las actualizaciones, sin considerar versiones publicadas hace menos de 14 días, y cada propuesta pasa por las pruebas antes de que decidas aceptarla.

**Lo que las pruebas no pueden decirte.** Como simulan las respuestas, no detectan que Wallapop o Vinted hayan cambiado su API: para eso está `/diagnostico`. Y lo que sí garantizan es que, cuando arregles un cambio de ese tipo, no rompas nada de lo demás.

## Licencia

Añade aquí la licencia que prefieras (por ejemplo, MIT).
