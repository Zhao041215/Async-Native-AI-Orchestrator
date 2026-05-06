from __future__ import annotations

from models import Project, Task


class Repository:
    def __init__(self) -> None:
        self.projects = [
            Project('demo', 'Demo Project', 'ready', 'platform', 3),
            Project('ops', 'Operations Console', 'active', 'ops', 8),
        ]
        self.tasks = [
            Task('task-1', 'Initial scaffold', 'ready', 'demo'),
            Task('task-2', 'Wire health checks', 'active', 'ops'),
        ]

    def list_projects(self) -> list[dict]:
        return [project.to_dict() for project in self.projects]

    def list_tasks(self) -> list[dict]:
        return [task.to_dict() for task in self.tasks]

    def health(self) -> dict:
        return {
            'ok': True,
            'service': 'api',
            'project_count': len(self.projects),
            'task_count': len(self.tasks),
        }
