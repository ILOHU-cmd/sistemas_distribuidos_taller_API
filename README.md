# Agregador Resiliente de APIs

API en FastAPI que combina dos dependencias externas:

- **USGS**: feed público de terremotos (última hora) y detalle del evento.
- **Servicio inestable local**: listado paginado de sensores.

El objetivo es exponer un endpoint agregado tolerante a fallos, con timeouts,
reintentos selectivos, caché con TTL e idempotencia en la creación de alertas.

## Integrantes y roles

| Integrante | Rol |
| --- | --- |
| Emerson | Cliente de APIs externas y resiliencia del servicio inestable |
| Nataly | Configuracion, modelos Pydantic y cache |
| Daniela | Endpoints propios, codigos HTTP e idempotencia |
| Laura | Integración servicio inestable, testeo, evidencias y documentacion del proyecto |

La API externa elegida es USGS. Su uso fue aprobado previamente por el docente
como API publica alternativa al catalogo del enunciado.

## Requisitos

- Python 3.11 o superior
- Las dependencias de `requirements.txt`

## Instalación y ejecución

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # ajustar valores si hace falta
uvicorn main:app --reload
```

La documentación interactiva queda en `http://localhost:8000/docs`.

Para ejecutar las pruebas automatizadas:

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Servicio inestable local

El endpoint agregado consulta un servicio de sensores en `http://localhost:9000`.
El archivo `servicio_inestable.py` se entrega aparte (no se versiona). Colócalo en
la raíz del proyecto y ejecútalo en otra terminal:

```bash
python servicio_inestable.py --semilla 42 --tasa-fallos 0.2 \
  --tasa-429 0.05 --tasa-lentitud 0.15 --latencia 1.5
```

Sus parámetros permiten simular `503`, `429` con `Retry-After`, respuestas lentas
y bloqueo por IP. Si no está corriendo, `GET /v1/monitoreo-riesgo` responde `502`.

## Configuración

Todas las variables se leen desde el entorno o desde `.env` (ver `.env.example`):

| Variable | Default | Descripción |
| --- | --- | --- |
| `APP_NAME` | `Agregador Resiliente de APIs` | Nombre de la aplicación |
| `ENVIRONMENT` | `development` | Entorno de ejecución |
| `UNSTABLE_SERVICE_URL` | `http://localhost:9000` | Base del servicio de sensores (`URL_INESTABLE`) |
| `USGS_FEED_URL` | feed `all_hour` de USGS | Feed GeoJSON de terremotos |
| `HTTP_TIMEOUT_SECONDS` | `5.0` | Timeout por petición HTTP |
| `UNSTABLE_TOTAL_TIMEOUT_SECONDS` | `20.0` | Deadline total del endpoint agregado |
| `MAX_RETRIES` | `5` | Intentos máximos ante 503/timeout (`MAX_INTENTOS`) |
| `BACKOFF_BASE` | `0.5` | Base del backoff exponencial |
| `CACHE_TTL_SECONDS` | `300` | TTL de la caché |

El contrato del cliente inestable define además `BACKOFF_TOPE=8.0`,
`TIMEOUT_CONEXION=2.0` y `TIMEOUT_LECTURA=2.0`. Las consultas simultáneas
para la misma clave de caché comparten un solo cálculo (single-flight).

## Endpoints

### `GET /salud`

Verificación simple del servicio.

```json
{ "estado": "ok" }
```

### `GET /v1/monitoreo-riesgo`

Consulta en paralelo el último sismo de USGS y todos los sensores, y devuelve:

- `generado_en`: timestamp UTC de la respuesta.
- `latencia_ms`: latencia de la consulta a USGS.
- `cache_hit`: si el sismo se obtuvo de la caché.
- `sismo`: feature GeoJSON del evento más reciente (o `null`).
- `sensores`: lista completa de sensores paginados.

Códigos de error:

- `404`: el recurso no existe en la dependencia externa.
- `502`: una dependencia devolvió una respuesta inválida o no disponible.
- `504`: se superó el deadline total configurado.

### `POST /v1/alertas`

Crea una alerta una sola vez por `Idempotency-Key`. Requiere la cabecera y el
siguiente cuerpo:

```json
{
  "sensor_id": "sensor-1",
  "tipo": "temperatura",
  "mensaje": "Valor fuera de rango",
  "severidad": "alta"
}
```

Repetir la petición con la misma clave devuelve exactamente la misma respuesta
(`201 Created`) sin volver a procesarla.

#### Prueba de idempotencia

```bash
KEY=demo-key-001
BODY='{"sensor_id":"S-001","tipo":"temperatura","mensaje":"Umbral","severidad":"alta"}'

# 1a vez -> 201 con un id
curl -s -X POST http://localhost:8000/v1/alertas \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" -d "$BODY"

# misma key + mismo cuerpo -> 201 con el MISMO id
curl -s -X POST http://localhost:8000/v1/alertas \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" -d "$BODY"

# misma key + cuerpo distinto -> 422
curl -s -X POST http://localhost:8000/v1/alertas \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"sensor_id":"S-002","tipo":"ruido","mensaje":"Otro","severidad":"baja"}'

# sin cabecera -> 422
curl -s -X POST http://localhost:8000/v1/alertas \
  -H "Content-Type: application/json" -d "$BODY"
```

Los mismos ejemplos están en `requests.http` para VS Code (REST Client).

La demostracion reproducible de idempotencia esta en
`tests/clients/evidencia_idempotencia.py`. La salida obtenida se conserva en
`evidencias/idempotencia_alertas.txt`: la primera y segunda respuesta tuvieron
estado `201` y el mismo `id`; reutilizar la clave con otro cuerpo respondio
`422`.

## Analisis de la Parte D

### D.a. Riesgo de reintentar un POST sin clave de idempotencia

Un timeout no permite saber si el servidor alcanzo a procesar el POST. Si el
cliente lo reintenta sin una clave, el servidor puede crear dos alertas para un
unico evento. `Idempotency-Key` permite reconocer la operacion original y
devolver la misma respuesta sin repetir el efecto.

### D.b. Cincuenta clientes reintentando sin jitter

Los clientes podrian reintentar simultaneamente en los mismos intervalos,
produciendo un efecto de estampida: picos de carga, mas respuestas `429` o
`503`, y una recuperacion mas lenta de la dependencia. El backoff exponencial
con jitter distribuye los reintentos en el tiempo.

### D.c. Desactualizacion aceptable de la cache

El TTL configurado es de 300 segundos, por lo que un resultado puede tener
hasta aproximadamente cinco minutos de antiguedad, ademas del tiempo de
propagacion de la respuesta. Es aceptable para un resumen de monitoreo, pero
seria inaceptable para decisiones de emergencia, cobros, controles de seguridad
o cualquier dato que deba reflejar el estado actual. En esos casos se deberia
reducir el TTL, invalidar explicitamente o consultar sin cache.

### D.d. Ejecucion detras de un balanceador

La cache y el almacen de idempotencia actuales viven en la memoria de cada
proceso. Con varias replicas, dos peticiones con la misma clave podrian llegar
a procesos distintos y crear efectos duplicados. Se necesitaria un almacen
compartido, por ejemplo Redis o una base de datos con restriccion unica para la
clave, ademas de una cache compartida o distribuida. Los locks locales no son
suficientes entre replicas.

## Contrato OpenAPI

El contrato que compartimos con otros grupos está en
[`docs/openapi.json`](docs/openapi.json). Se regenera desde el código con:

```bash
PYTHONPATH=. python scripts/exportar_openapi.py
```

Con la API en ejecución también está en `http://localhost:8000/openapi.json`
(Swagger UI en `/docs`). Incluye ejemplos de cuerpo, la descripción de la
cabecera `Idempotency-Key` y el esquema `ErrorResponse` (`{"detail": ...}`)
para los códigos `404`, `422`, `502` y `504`.

## Consumo del contrato de otro grupo

Consumimos el endpoint `<MÉTODO> <ruta>` del grupo **<nombre del grupo>**
usando **únicamente** su documentación OpenAPI (`<archivo o URL del contrato>`),
sin revisar su código ni preguntarles por otros medios.

Petición realizada:

```bash
curl -i -X <MÉTODO> <URL_BASE>/<ruta>   -H "Content-Type: application/json"   -d '<cuerpo según el contrato>'
```

Respuesta obtenida: `<código HTTP>` — `<resumen del cuerpo>`.

### Lo que le faltó al contrato

| Aspecto | Qué esperábamos encontrar | Impacto al consumirlo |
| --- | --- | --- |
| <p. ej. códigos de error> | <p. ej. esquema de la respuesta 503> | <p. ej. no sabíamos si reintentar> |
| <p. ej. ejemplos> | <p. ej. ejemplo de cuerpo válido> | <p. ej. varios 422 hasta acertar el formato> |
| <p. ej. cabeceras> | <p. ej. `Retry-After`, autenticación> | <...> |

### Lo que le sobró al contrato

| Aspecto | Qué sobraba | Por qué |
| --- | --- | --- |
| <p. ej. endpoint interno expuesto> | <...> | <p. ej. no es útil para el consumidor> |
| <p. ej. campos de respuesta> | <...> | <p. ej. nunca llegan o siempre son `null`> |

### Diferencias entre contrato y comportamiento real

- <p. ej. el contrato dice `200`, pero el servicio responde `201`>
- <p. ej. un campo marcado como requerido llega vacío>

## Estructura

```text
main.py                     # App FastAPI, lifespan y manejo de errores
scripts/exportar_openapi.py # Exporta el contrato a docs/openapi.json
docs/openapi.json           # Contrato OpenAPI para compartir
app/
  config.py                 # Configuración validada con pydantic-settings
  models.py                 # Modelos Pydantic (USGS, sensores, alertas)
  cache.py                  # Caché TTL y cálculo single-flight por clave
  clients/
    external_api.py         # Cliente USGS (feed + detalle)
    unstable_service.py     # Cliente con reintentos, Retry-After y paginación
  routers/
    agregador.py            # GET /v1/monitoreo-riesgo
    alertas.py              # POST /v1/alertas
```

## Comportamiento de resiliencia

- **Reintentos con backoff exponencial y jitter** ante `503` y timeouts.
- **`429`** respeta la cabecera `Retry-After`.
- **`400` y `404`** fallan de inmediato (no se reintentan).
- **Deadline total** con `asyncio.wait_for` sobre ambas dependencias.
- **Caché TTL** del último sismo para reducir llamadas a USGS; las peticiones
  concurrentes para una misma clave comparten la consulta en curso.
- **Idempotencia** protegida con un `asyncio.Lock` por clave en memoria del proceso.

> Nota: la caché y el almacén de idempotencia son por proceso. Para varias
> instancias haría falta un backend compartido (por ejemplo Redis).

## Evidencia de latencia (C8)

Medición real contra USGS con `PYTHONPATH=. python tests/clients/medir_latencia.py`
(5 muestras por modo):

| Modo | Muestras | Media (ms) | p95 (ms) |
|---|---:|---:|---:|
| Con caché | 5 | 0.002 | 0.003 |
| Sin caché | 5 | 479.529 | 1174.669 |

Detalle completo en `evidencias/latencia_cache.txt`. Los logs de los reintentos,
del backoff y del `Retry-After` están en `evidencias/logs_clients.txt`.
