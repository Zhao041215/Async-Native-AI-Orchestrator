from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict

ALLOWED_STATUS = {"pending", "done"}

def _now() -> str:
    return datetime.utcnow().isoformat()

@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: str = "pending"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        if not self.id or not self.id.strip():
            raise ValueError("Task id is required")
        if not self.title:
            raise ValueError("Task title is required")
        if self.status not in ALLOWED_STATUS:
            raise ValueError(f"status must be one of {sorted(ALLOWED_STATUS)}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            status=str(data.get("status", "pending")),
            created_at=str(data.get("created_at") or _now()),
            updated_at=str(data.get("updated_at") or _now()),
        )

    def touch(self) -> None:
        self.updated_at = _now()
