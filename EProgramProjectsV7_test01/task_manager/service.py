from .models import Task


class TaskManager:
    def __init__(self):
        self.tasks = []
        self._next_id = 1

    def add(self, title: str) -> Task:
        title = (title or '').strip()
        if not title:
            raise ValueError('title is required')
        task = Task(self._next_id, title)
        self.tasks.append(task)
        self._next_id += 1
        return task

    def list(self):
        return list(self.tasks)

    def _find(self, task_id: int) -> Task:
        for t in self.tasks:
            if t.id == task_id:
                return t
        raise ValueError(f'task {task_id} not found')

    def complete(self, task_id: int) -> Task:
        task = self._find(task_id)
        task.completed = True
        return task

    def delete(self, task_id: int) -> Task:
        task = self._find(task_id)
        self.tasks.remove(task)
        return task

    def update(self, task_id: int, title: str) -> Task:
        task = self._find(task_id)
        task.rename(title)
        return task
