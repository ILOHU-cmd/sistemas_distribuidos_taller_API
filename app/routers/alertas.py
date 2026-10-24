# app/routers/alertas.py
"""Endpoint idempotente para creación de alertas."""

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.models import AlertRequest, AlertResponse, ErrorResponse

router = APIRouter(prefix="/v1", tags=["alertas"])


@router.post(
    "/alertas", 
    response_model=AlertResponse, 
    status_code=status.HTTP_201_CREATED,
    summary="Crea una alerta de forma idempotente",
    responses={
        422: {
            "model": ErrorResponse,
            "description": "Falta la cabecera Idempotency-Key, el cuerpo es inválido "
            "o la misma clave fue reutilizada con un cuerpo diferente",
        },
        502: {"model": ErrorResponse, "description": "Error de dependencia externa"},
        504: {"model": ErrorResponse, "description": "Tiempo de espera agotado"},
        }
    )
async def crear_alerta(
    payload: AlertRequest,
    request: Request,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        description="Clave única por operación. Repetirla con el mismo cuerpo devuelve "
        "la misma alerta (201) sin crear otra; con otro cuerpo responde 422.",
        examples=["demo-key-001"],
    ),
) -> AlertResponse:
    """Procesa una clave una sola vez y devuelve exactamente el resultado guardado."""

    store: dict[str, AlertResponse] = request.app.state.idempotency_store
    locks: dict[str, asyncio.Lock] = request.app.state.idempotency_locks
    lock = locks.setdefault(idempotency_key, asyncio.Lock())

    async with lock:
        previous = store.get(idempotency_key)
        payload_data = payload.model_dump()
        if previous is not None:
            if previous["payload"] != payload_data:
                raise HTTPException(
                    status_code=422,
                    detail="La Idempotency-Key ya fue utilizada con un cuerpo diferente",
                )
            return previous["response"]
        
        response = AlertResponse(
            id=UUID(str(uuid4())),
            sensor_id=payload.sensor_id,
            tipo=payload.tipo,
            mensaje=payload.mensaje,
            severidad=payload.severidad,
            creada_en=datetime.now(timezone.utc),
        )
        
        store[idempotency_key] = {
            "payload": payload_data,
            "response": response
        }
        
        return response

