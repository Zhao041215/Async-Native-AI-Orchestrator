from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Project:
    id: str
    name: str
    status: str
    owner: str
    open_tasks: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    id: str
    title: str
    status: str
    project_id: str

    def to_dict(self) -> dict:
        return asdict(self)
