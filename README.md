# Agregador Resiliente de APIs

API en FastAPI que combina dos dependencias externas:

- **USGS**: feed público de terremotos (última hora) y detalle del evento.
- **Servicio inestable local**: listado paginado de sensores.

El objetivo es exponer un endpoint agregado tolerante a fallos, con timeouts,
reintentos selectivos, caché con TTL e idempotencia en la creación de alertas.

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

## Estructura

```text
main.py                     # App FastAPI, lifespan y manejo de errores
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
- **Idempotencia** protegida con `asyncio.Lock` en memoria del proceso.

> Nota: la caché y el almacén de idempotencia son por proceso. Para varias
> instancias haría falta un backend compartido (por ejemplo Redis).
