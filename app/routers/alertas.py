# app/routers/alertas.py
"""Endpoint idempotente para creación de alertas."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Request, status

from app.models import AlertRequest, AlertResponse

router = APIRouter(prefix="/v1", tags=["alertas"])


@router.post("/alertas", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def crear_alerta(
    payload: AlertRequest,
    request: Request,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
) -> AlertResponse:
    """Procesa una clave una sola vez y devuelve exactamente el resultado guardado."""

    store: dict[str, AlertResponse] = request.app.state.idempotency_store
    lock: asyncio.Lock = request.app.state.idempotency_lock
    async with lock:
        previous = store.get(idempotency_key)
        if previous is not None:
            return previous
        response = AlertResponse(
            id=UUID(str(uuid4())),
            sensor_id=payload.sensor_id,
            tipo=payload.tipo,
            mensaje=payload.mensaje,
            severidad=payload.severidad,
            creada_en=datetime.now(timezone.utc),
        )
        store[idempotency_key] = response
        return response

