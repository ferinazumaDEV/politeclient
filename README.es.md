<!-- synced-from: 6f05ffb241be7cd1dc8945f282f2a55a21bd9555 -->
# politeclient

**Español** · [English](README.md)

![Python](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

**Un cliente HTTP cuidadoso y bien educado para Python — todos esos comportamientos de buen ciudadano que reescribes en cada API nueva, en una envoltura pequeña sobre `requests`.**

Reintentos con espera exponencial **y jitter**, soporte de `Retry-After`, un gobernador de ritmo por host, un `User-Agent` honesto por defecto, caché opcional en disco para GETs, paginación por cursor y por desplazamiento, tiempos de espera sensatos y registro estructurado — detrás de una API limpia y pitónica.

Es una **pieza de construcción para quien programa**, no un scraper. Tú pones los endpoints; politeclient se encarga de que tu cliente se porte bien.

```python
from politeclient import PoliteClient, RateLimit, RetryPolicy

with PoliteClient(
    base_url="https://api.example.com",
    rate_limit=RateLimit(rate=5),           # 5 peticiones/segundo, por host
    retry=RetryPolicy(max_retries=4),       # espera + jitter, respeta Retry-After
    cache="~/.cache/politeclient",          # caché en disco opcional para GETs
) as client:
    usuarios = client.get("/users").json()
```

> **Nota sobre el idioma.** Este README está en las dos lenguas. El resto de la documentación y los comentarios del código están en inglés.

---

## Por qué

Casi todo «script rápido que habla con una API» acaba criando los mismos apéndices cuando llega a producción: un bucle de reintentos que se olvida del jitter y estampida al servidor, un `time.sleep(1)` que finge ser un límite de ritmo, una caché JSON hecha a mano con su condición de carrera, y el eterno *«¿por qué me da 403 en el script y no en el navegador?»* (pista: tu `User-Agent` dice `python-requests/2.x`).

`politeclient` empaqueta la versión correcta de cada una de esas cosas, una vez.

## Qué trae

- **Reintentos bien hechos** — espera exponencial con **jitter completo** (la estrategia [que recomienda AWS](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) contra la estampida), sólo para métodos idempotentes por defecto, con tope y configurable.
- **Entiende `Retry-After`** — cuando un servidor te dice cuándo volver (en segundos *o* como fecha HTTP), politeclient le hace caso en vez de adivinar (la espera se acota a `max_backoff`, 60 s por defecto).
- **Gobernador de ritmo por host** — un cubo de fichas seguro entre hilos por cada host, para que una API lenta y limitada no ahogue a una rápida. Admite ritmo sostenido más ráfagas.
- **`User-Agent` honesto por defecto** — envía un UA que identifica en lugar de `python-requests/x.y.z`, que es la causa más común de un `403` inesperado. Se cambia con un solo argumento.
- **Caché en disco opcional para GETs** — direccionada por contenido, con TTL y escrituras atómicas. Itera sobre un scraper sin machacar la API en cada pasada. **Las peticiones autenticadas no se cachean** salvo que lo pidas explícitamente. Guarda sólo una lista blanca de cabeceras de respuesta, se salta las marcadas `no-store` o con `Vary`, y deja que el `max-age`/`Expires` del servidor acorte el TTL — ver [Límites de la caché](#límites-de-la-caché).
- **Ayudas de paginación** — generadores perezosos para APIs de **cursor** y de **desplazamiento/límite**, con extracción por ruta con puntos (`items_key="data.results"`).
- **Tiempos de espera sensatos** — una petición sin timeout puede colgarse para siempre; politeclient usa `(5 s de conexión, 30 s de lectura)`.
- **Registro estructurado** — cada petición, reintento, espera y acierto de caché como una línea `clave=valor` que puedes grepear, o JSON por líneas (`POLITECLIENT_LOG=json`).
- **API limpia** — gestor de contexto, atajos por verbo y un decorador `@polite`.
- **Tipado y probado** — anotaciones completas, cero dependencias más allá de `requests`, y 92 pruebas contra un servidor local de mentira.

## Instalación

```bash
pip install politeclient
```

Necesita Python 3.9+ y `requests`. Ese es el árbol de dependencias entero.

## Uso

### El cliente

```python
client = PoliteClient(
    base_url="https://api.github.com",
    rate_limit=RateLimit(rate=10, per=1.0, burst=10),   # 10/s, ráfagas de hasta 10
    retry=RetryPolicy(max_retries=5, backoff_factor=0.5, max_backoff=30),
    user_agent="mi-app/1.0 (+https://mi-app.example)",
    timeout=(5, 30),
)
```

`base_url` se une a la ruta que le pases con `urllib.parse.urljoin`, así que aplican las reglas de siempre del RFC 3986. Un host pelado funciona de cualquier forma, pero una base con prefijo de ruta **tiene que acabar en `/`** y las rutas han de ser relativas:

```python
client = PoliteClient(base_url="https://api.example.com/v1/")
client.get("users")     # -> https://api.example.com/v1/users
client.get("/users")    # -> https://api.example.com/users  (la barra inicial vuelve a la raíz)
```

### El decorador `@polite`

Cuando prefieres no ir pasando un cliente de un lado a otro, `@polite` lo construye y lo inyecta:

```python
@polite(rate_limit=RateLimit(rate=5), base_url="https://api.example.com")
def trae_usuario(client, user_id):
    return client.get(f"/users/{user_id}").json()

trae_usuario(42)              # se inyecta el cliente compartido y limitado
trae_usuario.client.close()   # ...y lo tienes a mano al terminar
```

### Paginación

```python
# Desplazamiento / límite — para solo al llegar a la última página (más corta):
for fila in client.paginate_offset("/records", items_key="data", limit=100):
    procesa(fila)

# Cursor — sigue el token "next" hasta que se acaba:
for fila in client.paginate_cursor("/feed", items_key="items", cursor_key="paging.next"):
    procesa(fila)
```

Los dos son generadores perezosos, así que un `itertools.islice(...)` o un `break` temprano sólo descargan las páginas que consumes de verdad.

### Caché

```python
with PoliteClient(cache="~/.cache/miscraper", cache_ttl=3600) as client:
    a = client.get("/caro")          # red
    b = client.get("/caro")          # servido desde disco
    assert b.from_cache is True
```

### Límites de la caché

**Las peticiones con credenciales no se cachean.** La clave se construye con método, URL y parámetros — nunca con cabeceras, así que ninguna credencial llega jamás a un nombre de fichero. La consecuencia es que dos llamantes con tokens distintos producirían la **misma** clave, y a uno se le podría servir la respuesta personalizada del otro. En vez de meter credenciales en la clave, `politeclient` **se salta la caché entera** cuando la petición lleva credenciales por cualquiera de las vías que admite `requests`: una cabecera explícita `Authorization`, `Cookie`, `Proxy-Authorization` o `WWW-Authenticate` en la petición o en la sesión, un `auth=` o `cookies=` por petición, `session.auth`, un tarro de cookies de sesión no vacío (cualquier cookie, de cualquier dominio — por ejemplo una que dejara un login anterior), y `~/.netrc` cuando la sesión tiene `trust_env` activo (que es lo normal en `requests`). La comprobación es deliberadamente conservadora: **ante la duda, no se escribe nada.**

Si sabes que una respuesta autenticada concreta es idéntica para todos, lo pides por petición:

```python
client.request("GET", url, headers={"Authorization": token}, use_cache=True)
```

Eso es una declaración deliberada, no un valor por defecto.

La caché está **apagada por defecto**; sólo existe si pasas `cache=`. Cuando la enciendes, es exactamente esto — una caché privada pequeña para iterar sobre un script, no una implementación de caché HTTP:

- **La clave es `método + URL + sorted(parámetros)`, y nada más.** Ni cabeceras, ni cookies, ni credenciales — que es también por lo que una respuesta con `Vary` no se cachea en absoluto: la clave no sabe distinguir una variante de otra, así que devolverla podría darte la equivocada.
- **Las entradas son ficheros JSON planos y sin cifrar** (el cuerpo va en base64, que es codificación, no protección) en el directorio que **tú** elijas. Ese camino y sus permisos son tuyos — ver [SECURITY.md](SECURITY.md).
- **Sólo se guardan estas cabeceras de respuesta:** `Content-Type`, `Content-Encoding`, `ETag`, `Last-Modified`, `Date`, `Vary`. Todo lo demás — `Set-Cookie`, `Authorization`, `WWW-Authenticate` y cualquier cabecera en la que nadie pensó — se descarta antes de llegar al disco.
- **`Cache-Control: no-store` se respeta al escribir**, y `no-cache` se trata como «nunca fresco», porque esta caché no sabe revalidar.
- **El `max-age`/`Expires` del servidor es un tope de tu TTL.** Gana el más corto de los dos; `cache_ttl` sólo puede hacer que una entrada caduque **antes** de lo que dijo el servidor, nunca después.
- **No es RFC 9111.** Sin revalidación con `ETag`/`Last-Modified`, sin `stale-while-revalidate`, sin semántica de caché compartida. Si necesitas eso, pon un proxy de caché de verdad delante.

## Demostración

`examples/demo.py` es autocontenido: levanta un servidor local que se porta mal a propósito (429 con `Retry-After`, paginación por desplazamiento, un endpoint cacheable) y lo maneja. Sin red y sin claves.

## Cómo funciona

Cada `request()` pasa por la misma tubería:

1. **Consulta de caché** (sólo GET) — clave direccionada por contenido sobre `método + url + sorted(parámetros)`; un acierto fresco cortocircuita todo y devuelve una respuesta con `from_cache is True`. «Fresco» es el menor entre tu TTL y la frescura que declaró el servidor.
2. **Puerta de ritmo** — la petición coge una ficha del cubo de su host, bloqueándose justo lo necesario si está vacío. Los cubos se rellenan de forma perezosa (sin hilos de fondo): cada toma calcula cuántas fichas *habrían* caído desde la última vez.
3. **Enviar y evaluar** — ante un estado reintentable (`429`, `5xx`) o un error de transporte pasajero (conexión cortada, timeout), calcula la siguiente espera. `Retry-After` manda cuando está y es válido (acotado a `max_backoff`); si no, es `backoff_factor · 2ⁿ` con tope en `max_backoff`, y luego el **jitter completo** elige un punto al azar en `[0, eso]`.
4. **Reintentar o devolver** — los métodos no idempotentes (`POST`) no se reintentan por defecto, porque reintentarlos puede duplicar trabajo. Agotado el presupuesto, un fallo HTTP se devuelve tal cual (para que puedas hacer `raise_for_status()`), mientras que un fallo de transporte lanza `RetryBudgetExceeded`.
5. **Guardar** — un GET correcto se escribe en la caché de forma atómica (fichero temporal + `os.replace`), guardando sólo las cabeceras de la lista blanca y saltándose las respuestas marcadas `no-store` o con `Vary`.

El cubo de fichas, la política de reintentos, la caché y la paginación son piezas independientes e importables (`TokenBucket`, `RetryPolicy`, `DiskCache`, `paginate_cursor`), así que puedes reutilizar una sin comprar el cliente entero.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest                     # 92 pruebas, todas sin red
python examples/demo.py    # el recorrido de arriba
```

La batería levanta un pequeño servidor HTTP programable (`tests/conftest.py`) y le guiona secuencias de fallo exactas — tres 429 y luego un 200, una tormenta de 500, una cabecera `Retry-After`, conjuntos paginados — para que los reintentos, la espera, el límite de ritmo y la caché se verifiquen contra sockets reales, de forma determinista y sin tocar la red.

## Parte de una familia de herramientas pequeñas

politeclient es una de una familia de piezas pequeñas y concretas que mantengo para quien programa en Python. Su comportamiento de buen ciudadano —`User-Agent` honesto, espera entre reintentos y límite de ritmo por host— es también la higiene básica que se espera de un crawler o un bot de IA bien educado, que es donde roza de refilón el GEO técnico (optimización para motores generativos).

- [The GEO Handbook](https://github.com/ferinazumaDEV/generative-engine-optimization-handbook) — la referencia abierta sobre conseguir que los motores de respuesta de IA te citen.
- [webhook-replay](https://github.com/ferinazumaDEV/webhook-replay) — captura un webhook una vez y reprodúcelo contra tu aplicación local las veces que haga falta; la otra mitad del kit de «HTTP que se porta bien».
- [typedout](https://github.com/ferinazumaDEV/typedout) — salida estructurada fiable de OpenAI y Anthropic, con interfaz de proveedor para añadir otros.
- [scaffld](https://github.com/ferinazumaDEV/scaffld) — genera proyectos Python completos (pruebas, CI, pre-commit, licencia) desde plantillas, con interfaz de terminal.
- Web y publicaciones: [zentimes.es](https://zentimes.es).

De [ferinazumaDEV](https://github.com/ferinazumaDEV).

## Licencia

MIT — ver [LICENSE](LICENSE).

---

*Hecho por Fernando Aporta Franco ([@ferinazumaDEV](https://github.com/ferinazumaDEV)).*
