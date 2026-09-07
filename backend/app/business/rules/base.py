from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from backend.app.business.rules.result import RuleResult

ContextT = TypeVar("ContextT", contravariant=True)


@dataclass(frozen=True)
class RuleInfo:
    code: str
    name: str
    description: str


class Rule(Protocol[ContextT]):
    info: RuleInfo

    def evaluate(self, context: ContextT) -> RuleResult:
        ...
