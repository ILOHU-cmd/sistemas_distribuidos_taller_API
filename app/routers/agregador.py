# app/routers/agregador.py
"""Endpoint que agrega USGS y sensores locales."""

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.clients.external_api import ExternalApiError, ExternalEarthquakeClient
from app.clients.unstable_service import NoEncontrado, UnstableServiceError
from app.models import ErrorResponse, RiskMonitoringResponse

router = APIRouter(prefix="/v1", tags=["monitoreo"])


@router.get(
    "/monitoreo-riesgo", 
    response_model=RiskMonitoringResponse,
    summary="Monitoreo de riesgo sísmico",
    responses={
        404: {"model": ErrorResponse, "description": "Recurso no encontrado"},
        502: {"model": ErrorResponse, "description": "Error no recuperable en dependencia externa"},
        504: {"model": ErrorResponse, "description": "Tiempo de espera agotado en dependencia externa"},
        }
    )
async def monitoreo_riesgo(request: Request) -> RiskMonitoringResponse:
    """Combina ambas dependencias; traduce fallos de dependencia a 502/504."""

    external: ExternalEarthquakeClient = request.app.state.external_client
    unstable = request.app.state.unstable_client
    try:
        external_result, sensors = await asyncio.wait_for(
            asyncio.gather(external.get_latest_event(), unstable.get_all_sensors()),
            timeout=request.app.state.settings.unstable_total_timeout_seconds,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise HTTPException(status_code=504, detail="Una dependencia excedió el tiempo total permitido") from exc
    
    except NoEncontrado as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    
    except (ExternalApiError, UnstableServiceError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return RiskMonitoringResponse(
        generado_en=datetime.now(timezone.utc),
        latencia_ms=round(external_result.latency_ms, 3),
        cache_hit=external_result.cache_hit,
        sismo=external_result.feature,
        sensores=sensors,
    )

