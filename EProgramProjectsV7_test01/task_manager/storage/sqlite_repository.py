from __future__ import annotations
import json, os, sqlite3
from typing import Any, Dict, List, Optional
from .repository import TaskRepository, Task

class SQLiteTaskRepository(TaskRepository):
    def __init__(self, db_path: str = "data/tasks.sqlite3"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )""")

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "completed": bool(row["completed"]),
            "metadata": json.loads(row["metadata"] or "{}"),
        }

    def create(self, task: Task) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks(title,description,completed,metadata) VALUES(?,?,?,?)",
                (
                    task["title"],
                    task.get("description", ""),
                    int(bool(task.get("completed", False))),
                    json.dumps(task.get("metadata", {})),
                ),
            )
            return int(cur.lastrowid)

    def get(self, task_id: int) -> Optional[Task]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return self._row_to_task(row) if row else None

    def list(self) -> List[Task]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
            return [self._row_to_task(r) for r in rows]

    def update(self, task_id: int, updates: Dict[str, Any]) -> bool:
        current = self.get(task_id)
        if not current: return False
        current.update(updates)
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET title=?,description=?,completed=?,metadata=? WHERE id=?",
                (
                    current["title"], current.get("description", ""),
                    int(bool(current.get("completed", False))),
                    json.dumps(current.get("metadata", {})), task_id,
                ),
            )
            return True

    def delete(self, task_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            return cur.rowcount > 0
