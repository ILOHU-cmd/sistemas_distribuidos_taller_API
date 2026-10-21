# app/models.py
"""Modelos de entrada y salida con validación estricta de Pydantic v2."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class UsgsModel(StrictModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class UsgsGeometry(UsgsModel):
    type: StrictStr
    coordinates: list[float | int]


class UsgsProperties(UsgsModel):
    mag: StrictFloat | StrictInt | None = None
    place: StrictStr | None = None
    time: StrictInt | None = None
    updated: StrictInt | None = None
    url: StrictStr | None = None
    detail: StrictStr | None = None
    status: StrictStr | None = None
    tsunami: StrictInt | None = None
    title: StrictStr | None = None


class UsgsFeature(UsgsModel):
    type: StrictStr
    id: StrictStr
    properties: UsgsProperties
    geometry: UsgsGeometry | None = None


class UsgsFeed(UsgsModel):
    type: StrictStr
    metadata: dict[str, Any]
    features: list[UsgsFeature]


class Sensor(StrictModel):
    """Representa un sensor devuelto por el servicio inestable."""

    id: StrictStr
    tipo: StrictStr | None = None
    nombre: StrictStr | None = None
    valor: StrictFloat | StrictInt | None = None
    unidad: StrictStr | None = None
    estado: StrictStr | None = None
    ubicacion: StrictStr | None = None


class SensorPage(StrictModel):
    sensores: list[Sensor]
    siguiente: StrictStr | None = None


class RiskMonitoringResponse(StrictModel):
    generado_en: datetime
    latencia_ms: StrictFloat
    cache_hit: bool
    sismo: UsgsFeature | None
    sensores: list[Sensor]


class AlertRequest(StrictModel):
    sensor_id: StrictStr = Field(min_length=1)
    tipo: StrictStr = Field(min_length=1)
    mensaje: StrictStr = Field(min_length=1)
    severidad: StrictStr = Field(min_length=1)


class AlertResponse(StrictModel):
    id: UUID
    sensor_id: StrictStr
    tipo: StrictStr
    mensaje: StrictStr
    severidad: StrictStr
    creada_en: datetime
