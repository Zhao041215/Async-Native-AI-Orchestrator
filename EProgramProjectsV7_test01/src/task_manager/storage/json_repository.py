from __future__ import annotations
import json
from pathlib import Path
from typing import List, Optional
from task_manager.models.task import Task
from task_manager.storage.repository import TaskRepository

class JsonTaskRepository(TaskRepository):
    def __init__(self, file_path: str = "data/tasks.json") -> None:
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._write([])

    def _read(self) -> List[Task]:
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8") or "[]")
            return [Task.from_dict(x) for x in data]
        except (json.JSONDecodeError, OSError, ValueError):
            return []

    def _write(self, tasks: List[Task]) -> None:
        payload = [t.to_dict() for t in tasks]
        self.file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_tasks(self) -> List[Task]:
        return self._read()

    def get_task(self, task_id: str) -> Optional[Task]:
        return next((t for t in self._read() if t.id == task_id), None)

    def save_task(self, task: Task) -> Task:
        tasks = self._read()
        task.touch()
        for i, existing in enumerate(tasks):
            if existing.id == task.id:
                task.created_at = existing.created_at
                tasks[i] = task
                self._write(tasks)
                return task
        tasks.append(task)
        self._write(tasks)
        return task

    def delete_task(self, task_id: str) -> bool:
        tasks = self._read()
        kept = [t for t in tasks if t.id != task_id]
        if len(kept) == len(tasks):
            return False
        self._write(kept)
        return True
