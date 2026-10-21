# tests/clients/test_external_api.py
"""Pruebas del cliente de USGS: llamadas encadenadas y caché (respx, sin red)."""

import httpx
import pytest
import respx

import app.clients.external_api as ex
from app.clients import RespuestaInvalida, TimeoutDependencia

FEED_URL = "http://usgs.local/feed"
DETALLE_URL = "http://usgs.local/detail/us1"

FEED = {
    "type": "FeatureCollection",
    "metadata": {"titulo": "all_hour"},
    "features": [
        {
            "type": "Feature",
            "id": "us1",
            "properties": {"detail": DETALLE_URL, "mag": 5.0, "place": "X"},
            "geometry": {"type": "Point", "coordinates": [-73.0, 7.0]},
        }
    ],
}

DETALLE = {
    "type": "Feature",
    "id": "us1",
    "properties": {
        "detail": DETALLE_URL,
        "mag": 5.0,
        "place": "X",
        "title": "M 5.0 - X",
    },
    "geometry": {"type": "Point", "coordinates": [-73.0, 7.0]},
}


class CacheFalsa:
    """Doble de ``app.cache.MemoryCache`` que cuenta lecturas y escrituras."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_segundos = ttl_seconds
        self.datos: dict[str, object] = {}
        self.escrituras = 0

    async def get(self, clave: str):
        return self.datos.get(clave)

    async def set(self, clave: str, valor: object) -> None:
        self.escrituras += 1
        self.datos[clave] = valor


@pytest.mark.asyncio
async def test_encadena_dos_llamadas_y_usa_cache(monkeypatch):
    """Feed -> detalle una sola vez; la segunda consulta sale de la caché."""

    monkeypatch.setattr(ex, "MemoryCache", CacheFalsa)
    async with httpx.AsyncClient() as client:
        with respx.mock:
            ruta_feed = respx.get(FEED_URL).mock(
                return_value=httpx.Response(200, json=FEED)
            )
            ruta_detalle = respx.get(DETALLE_URL).mock(
                return_value=httpx.Response(200, json=DETALLE)
            )
            api = ex.ExternalEarthquakeClient(client, FEED_URL, 5.0, 300)
            primero = await api.get_latest_event()
            segundo = await api.get_latest_event()

    assert ruta_feed.call_count == 1
    assert ruta_detalle.call_count == 1
    assert primero.cache_hit is False
    assert segundo.cache_hit is True
    assert primero.feature is not None
    assert primero.feature.properties.mag == 5.0
    assert segundo.feature is not None
    assert segundo.feature.id == "us1"


@pytest.mark.asyncio
async def test_sin_detalle_devuelve_el_evento_del_feed(monkeypatch):
    """Si el evento no trae URL de detalle, se devuelve lo del feed."""

    monkeypatch.setattr(ex, "MemoryCache", CacheFalsa)
    feed_sin_detalle = {
        "type": "FeatureCollection",
        "metadata": {},
        "features": [
            {
                "type": "Feature",
                "id": "us2",
                "properties": {"mag": 3.0},
                "geometry": None,
            }
        ],
    }
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(FEED_URL).mock(
                return_value=httpx.Response(200, json=feed_sin_detalle)
            )
            api = ex.ExternalEarthquakeClient(client, FEED_URL, 5.0, 300)
            resultado = await api.get_latest_event()

    assert resultado.cache_hit is False
    assert resultado.feature is not None
    assert resultado.feature.id == "us2"


@pytest.mark.asyncio
async def test_feed_invalido_lanza_respuestainvalida(monkeypatch):
    """Un feed que no cumple ``UsgsFeed`` se rechaza con RespuestaInvalida."""

    monkeypatch.setattr(ex, "MemoryCache", CacheFalsa)
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(FEED_URL).mock(
                return_value=httpx.Response(200, json={"type": "FeatureCollection"})
            )
            api = ex.ExternalEarthquakeClient(client, FEED_URL, 5.0, 300)
            with pytest.raises(RespuestaInvalida):
                await api.get_latest_event()


@pytest.mark.asyncio
async def test_timeout_lanza_timeoutdependencia(monkeypatch):
    """Un timeout del feed se traduce a TimeoutDependencia."""

    monkeypatch.setattr(ex, "MemoryCache", CacheFalsa)
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(FEED_URL).mock(side_effect=httpx.ConnectTimeout("lento"))
            api = ex.ExternalEarthquakeClient(client, FEED_URL, 5.0, 300)
            with pytest.raises(TimeoutDependencia):
                await api.get_latest_event()


@pytest.mark.asyncio
async def test_obtener_ultimo_sismo_devuelve_hit(monkeypatch):
    """La función de conveniencia devuelve ``(sismo, fue_hit)``."""

    monkeypatch.setattr(ex, "MemoryCache", CacheFalsa)
    async with httpx.AsyncClient() as client:
        with respx.mock:
            respx.get(FEED_URL).mock(return_value=httpx.Response(200, json=FEED))
            respx.get(DETALLE_URL).mock(
                return_value=httpx.Response(200, json=DETALLE)
            )
            sismo_uno, hit_uno = await ex.obtener_ultimo_sismo(client, FEED_URL)
            sismo_dos, hit_dos = await ex.obtener_ultimo_sismo(client, FEED_URL)

    assert hit_uno is False
    assert hit_dos is True
    assert sismo_dos is not None
    assert sismo_dos.id == "us1"
