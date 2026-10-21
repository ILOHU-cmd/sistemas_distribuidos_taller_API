# tests/clients/medir_latencia.py
"""Mide la latencia de la API externa con y sin caché (media y p95).

Se ejecuta a mano, contra la API real:
``PYTHONPATH=. python tests/clients/medir_latencia.py``
"""

import asyncio
import os

import httpx

from app.clients.external_api import ExternalEarthquakeClient

URL_EXTERNA = os.environ.get(
    "URL_EXTERNA",
    "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson",
)
MUESTRAS = int(os.environ.get("MUESTRAS", "5"))


async def main() -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        api = ExternalEarthquakeClient(client, URL_EXTERNA, 5.0, 300)
        resultado = await api.medir_latencia(MUESTRAS)

    print(f"API externa: {URL_EXTERNA}")
    print(f"muestras por modo: {MUESTRAS}")
    print(f"{'modo':<12}{'muestras':>10}{'media_ms':>12}{'p95_ms':>12}")
    for modo, resumen in resultado.items():
        print(
            f"{modo:<12}{resumen.muestras:>10}"
            f"{resumen.media_ms:>12.3f}{resumen.p95_ms:>12.3f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
