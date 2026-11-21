"""Application error hierarchy. Services raise these; the API layer maps them to HTTP.

Domain code never imports FastAPI, so it raises AppError subclasses carrying a
stable machine-readable `code` and an HTTP status hint.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    message: str = "Internal server error"

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    code, status_code, message = "not_found", 404, "Resource not found"


class ConflictError(AppError):
    code, status_code, message = "conflict", 409, "Resource state conflict"


class ValidationFailedError(AppError):
    code, status_code, message = "validation_failed", 422, "Validation failed"


class AuthenticationError(AppError):
    code, status_code, message = "unauthenticated", 401, "Authentication required"


class PermissionDeniedError(AppError):
    code, status_code, message = "forbidden", 403, "Not allowed"


class DependencyUnavailableError(AppError):
    code, status_code, message = "dependency_unavailable", 503, "A required dependency is unavailable"
