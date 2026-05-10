from dataclasses import dataclass


@dataclass
class Task:
    id: int
    title: str
    completed: bool = False

    def rename(self, title: str):
        title = (title or '').strip()
        if not title:
            raise ValueError('title is required')
        self.title = title
