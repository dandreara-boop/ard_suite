from __future__ import annotations

from backend.app.events.base import DomainEvent


class InventoryMovementRequested(DomainEvent):
    def __init__(self, aggregate_type: str, aggregate_id: str, payload: dict) -> None:
        super().__init__(
            event_type="InventoryMovementRequested",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
        )
