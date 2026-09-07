from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuleStatus(str, Enum):
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"


@dataclass(frozen=True)
class RuleResult:
    status: RuleStatus
    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        return self.status == RuleStatus.ALLOWED

    @property
    def requires_authorization(self) -> bool:
        return self.status == RuleStatus.REQUIRES_AUTHORIZATION

    @classmethod
    def allowed(cls, code: str, message: str, data: dict[str, Any] | None = None) -> RuleResult:
        return cls(
            status=RuleStatus.ALLOWED,
            code=code,
            message=message,
            data=data or {},
        )

    @classmethod
    def denied(cls, code: str, message: str, data: dict[str, Any] | None = None) -> RuleResult:
        return cls(
            status=RuleStatus.DENIED,
            code=code,
            message=message,
            data=data or {},
        )

    @classmethod
    def requires_auth(
        cls, code: str, message: str, data: dict[str, Any] | None = None
    ) -> RuleResult:
        return cls(
            status=RuleStatus.REQUIRES_AUTHORIZATION,
            code=code,
            message=message,
            data=data or {},
        )
