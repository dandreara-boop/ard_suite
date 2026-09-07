from __future__ import annotations

from typing import Any

from backend.app.business.rules import RuleStatus


class CatalogError(Exception):
    pass


class NotFoundError(CatalogError):
    pass


class ConflictError(CatalogError):
    pass


class ValidationError(CatalogError):
    pass


class BusinessRuleViolation(CatalogError):
    def __init__(
        self,
        code: str,
        message: str,
        status: RuleStatus,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.data = data or {}
