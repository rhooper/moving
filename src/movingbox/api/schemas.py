"""Request bodies.

All models forbid unknown fields. That is what makes a PATCH carrying `status`
fail loudly (422) instead of being silently ignored: status and location must go
through their own endpoints so every transition reaches the event log.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..store import STATUSES

Status = Literal[STATUSES]  # type: ignore[valid-type]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoxWrite(Strict):
    destination_room_id: int | None = None
    source_room_id: int | None = None
    source_location: str | None = None
    content_summary: str | None = None
    notes: str | None = None
    fragile: bool | None = None
    open_first: bool | None = None
    heavy: bool | None = None
    weight_kg: float | None = None
    group_name: str | None = None
    group_index: int | None = None
    group_total: int | None = None

    def set_fields(self) -> dict:
        """Only the fields the caller actually sent."""
        return self.model_dump(exclude_unset=True)


class StatusChange(Strict):
    status: Status
    actor: str | None = None


class LocationChange(Strict):
    current_location: str | None
    actor: str | None = None


class ItemCreate(Strict):
    name: str = Field(min_length=1)
    qty: int = 1
    category: str | None = None
    est_value: float | None = None
    notes: str | None = None
    source: Literal["manual", "ai"] = "manual"


class RoomCreate(Strict):
    name: str = Field(min_length=1)
    kind: Literal["source", "destination", "both"] = "destination"
    color_hex: str | None = None
    sort_order: int = 0
    notes: str | None = None
