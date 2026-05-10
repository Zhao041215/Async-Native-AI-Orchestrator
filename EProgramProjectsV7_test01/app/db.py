import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import current_app, g


def _get_database_path() -> str:
    configured_path = current_app.config.get("DATABASE")
    if configured_path:
        return configured_path

    instance_path = Path(current_app.instance_path)
    instance_path.mkdir(parents=True, exist_ok=True)
    return str(instance_path / "todos.sqlite")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        database_path = _get_database_path()
        parent = Path(database_path).parent
        parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        g.db = connection

    return g.db


def close_db(e: Optional[BaseException] = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    with current_app.open_resource("schema.sql") as schema_file:
        db.executescript(schema_file.read().decode("utf-8"))
    db.commit()


def init_app(app: Any) -> None:
    app.teardown_appcontext(close_db)


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
    }


def list_todos() -> List[Dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, title, completed, created_at
        FROM todos
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def get_todo(todo_id: int) -> Optional[Dict[str, Any]]:
    row = get_db().execute(
        """
        SELECT id, title, completed, created_at
        FROM todos
        WHERE id = ?
        """,
        (todo_id,),
    ).fetchone()

    if row is None:
        return None

    return row_to_dict(row)


def create_todo(title: str) -> Dict[str, Any]:
    cleaned_title = title.strip()
    if not cleaned_title:
        raise ValueError("title cannot be empty")

    db = get_db()
    cursor = db.execute(
        "INSERT INTO todos (title, completed) VALUES (?, ?)",
        (cleaned_title, 0),
    )
    db.commit()
    return get_todo(cursor.lastrowid)  # type: ignore[arg-type]


def update_todo(todo_id: int, title: str) -> Optional[Dict[str, Any]]:
    cleaned_title = title.strip()
    if not cleaned_title:
        raise ValueError("title cannot be empty")

    db = get_db()
    cursor = db.execute(
        "UPDATE todos SET title = ? WHERE id = ?",
        (cleaned_title, todo_id),
    )
    db.commit()

    if cursor.rowcount == 0:
        return None

    return get_todo(todo_id)


def set_todo_completed(todo_id: int, completed: bool) -> Optional[Dict[str, Any]]:
    db = get_db()
    cursor = db.execute(
        "UPDATE todos SET completed = ? WHERE id = ?",
        (1 if completed else 0, todo_id),
    )
    db.commit()

    if cursor.rowcount == 0:
        return None

    return get_todo(todo_id)


def toggle_todo(todo_id: int) -> Optional[Dict[str, Any]]:
    db = get_db()
    cursor = db.execute(
        "UPDATE todos SET completed = CASE completed WHEN 1 THEN 0 ELSE 1 END WHERE id = ?",
        (todo_id,),
    )
    db.commit()

    if cursor.rowcount == 0:
        return None

    return get_todo(todo_id)


def delete_todo(todo_id: int) -> bool:
    db = get_db()
    cursor = db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db.commit()
    return cursor.rowcount > 0
