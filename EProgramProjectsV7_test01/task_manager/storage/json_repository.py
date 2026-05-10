from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from task_manager.models.task import Task
from task_manager.storage.repository import TaskRepository


class JsonTaskRepository(TaskRepository):
    def __init__(self, path: str = "data/tasks.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _write(self, items: list[dict]) -> None:
        self.path.write_text(json.dumps(items, indent=2), encoding="utf-8")

    def list_tasks(self) -> list[Task]:
        return [Task.from_dict(x) for x in self._read()]

    def get_task(self, task_id: str) -> Optional[Task]:
        for item in self._read():
            if item.get("id") == task_id:
                return Task.from_dict(item)
        return None

    def save_task(self, task: Task) -> Task:
        task.validate()
        now = datetime.utcnow().isoformat()
        items = self._read()
        found = False
        for i, item in enumerate(items):
            if item.get("id") == task.id:
                task.created_at = item.get("created_at", task.created_at)
                task.updated_at = now
                items[i] = task.to_dict()
                found = True
                break
        if not found:
            task.created_at = task.created_at or now
            task.updated_at = now
            items.append(task.to_dict())
        self._write(items)
        return task

    def delete_task(self, task_id: str) -> bool:
        items = self._read()
        kept = [x for x in items if x.get("id") != task_id]
        if len(kept) == len(items):
            return False
        self._write(kept)
        return True
