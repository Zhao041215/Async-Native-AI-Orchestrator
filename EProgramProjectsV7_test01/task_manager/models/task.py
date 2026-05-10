from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.utcnow().isoformat()


@dataclass
class Task:
    title: str
    id: str
    completed: bool = False
    description: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def validate(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id is required")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title is required")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "completed": self.completed,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        task = cls(
            id=str(data.get("id", "")).strip(),
            title=str(data.get("title", "")).strip(),
            description=str(data.get("description", "")),
            completed=bool(data.get("completed", False)),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
        )
        task.validate()
        return task
