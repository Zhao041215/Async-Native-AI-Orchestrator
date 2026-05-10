from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional
from task_manager.models.task import Task

class TaskRepository(ABC):
    @abstractmethod
    def list_tasks(self) -> List[Task]: ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Task]: ...

    @abstractmethod
    def save_task(self, task: Task) -> Task: ...

    @abstractmethod
    def delete_task(self, task_id: str) -> bool: ...
