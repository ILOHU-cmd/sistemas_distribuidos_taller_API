# app/clients/__init__.py
"""Clientes de las dependencias externas y su jerarquía de errores.

Este paquete es el contrato que consumen ``main.py`` y ``app/routers``. Las
excepciones se definen aquí (y NO en los submódulos) para evitar importaciones
circulares: los submódulos hacen ``from app.clients import ...``.

Compatibilidad: ``UnstableServiceError`` y ``ExternalApiError`` se conservan
porque ``main.py`` y el router de Daniela ya las importan y las traducen a
502/504. Las excepciones nuevas heredan de ellas, de modo que el router sigue
funcionando sin cambios.
"""


class ErrorDependencia(Exception):
    """Fallo de una dependencia que no se puede resolver (el router -> 502).

    Se usa cuando se agotan los reintentos por 503/429, cuando la dependencia
    devuelve un estado inesperado o cuando la respuesta no es utilizable.
    """


class UnstableServiceError(ErrorDependencia):
    """Error del servicio inestable. Compatibilidad con ``main.py``/router (502)."""


class ExternalApiError(ErrorDependencia):
    """Error de la API externa. Compatibilidad con ``main.py``/router (502)."""


class TimeoutDependencia(UnstableServiceError, TimeoutError):
    """La dependencia no respondió dentro del tiempo permitido (router -> 504).

    Hereda también de ``TimeoutError`` para que el router lo capture como 504.
    """


class NoEncontrado(UnstableServiceError):
    """El recurso no existe en la dependencia (404 de la dependencia -> 404).

    Nunca se reintenta: es un error legítimo del cliente.
    """


class RespuestaInvalida(UnstableServiceError):
    """La respuesta no cumple el modelo Pydantic esperado (router -> 502)."""


__all__ = [
    "ErrorDependencia",
    "ExternalApiError",
    "NoEncontrado",
    "RespuestaInvalida",
    "TimeoutDependencia",
    "UnstableServiceError",
]
