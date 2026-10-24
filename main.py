# main.py
"""Punto de entrada de la API Agregador Resiliente de APIs."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.clients.external_api import ExternalEarthquakeClient
from app.clients.unstable_service import UnstableServiceClient
from app.config import Settings, get_settings
from app.routers import agregador, alertas


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = get_settings()
    client = httpx.AsyncClient()
    app.state.settings = settings
    app.state.external_client = ExternalEarthquakeClient(
        client, str(settings.usgs_feed_url), settings.http_timeout_seconds, settings.cache_ttl_seconds
    )
    app.state.unstable_client = UnstableServiceClient(
        client,
        str(settings.unstable_service_url),
        settings.http_timeout_seconds,
        settings.max_retries,
        settings.backoff_base,
    )
    app.state.idempotency_store = {}
    app.state.idempotency_locks = {}
    import asyncio

    app.state.idempotency_lock = asyncio.Lock()
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(
    title="Agregador Resiliente de APIs",
    version="1.0.0",
    description=(
        "Combina el feed de sismos de USGS con sensores de un servicio local inestable "
        "y permite crear alertas idempotentes. Los errores se devuelven como "
        "`{\"detail\": \"...\"}`."
    ),
    servers=[{"url": "http://localhost:8000", "description": "Ejecución local"}],
    lifespan=lifespan,
)
app.include_router(agregador.router)
app.include_router(alertas.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Evita exponer trazas y garantiza una respuesta JSON controlada."""

    return JSONResponse(status_code=502, content={"detail": "Dependencia o error interno no recuperable"})


@app.get("/salud", tags=["sistema"])
async def salud() -> dict[str, str]:
    return {"estado": "ok"}

