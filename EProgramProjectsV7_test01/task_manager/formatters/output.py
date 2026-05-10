from typing import Iterable, Mapping, Any


def _task_line(task: Mapping[str, Any], idx: int | None = None) -> str:
    mark = "x" if task.get("completed") else " "
    title = str(task.get("title") or task.get("name") or "Untitled")
    task_id = task.get("id")
    prefix = f"{idx}. " if idx is not None else ""
    meta = f" (id={task_id})" if task_id is not None else ""
    return f"{prefix}[{mark}] {title}{meta}"


def format_task_list(tasks: Iterable[Mapping[str, Any]]) -> str:
    items = list(tasks)
    if not items:
        return "No tasks found."
    return "\n".join(_task_line(t, i) for i, t in enumerate(items, 1))


def format_task_table(tasks: Iterable[Mapping[str, Any]]) -> str:
    items = list(tasks)
    if not items:
        return "No tasks found."
    rows = [(str(t.get("id", "-")), "done" if t.get("completed") else "todo", str(t.get("title") or t.get("name") or "Untitled")) for t in items]
    w1 = max(len("ID"), *(len(r[0]) for r in rows))
    w2 = max(len("Status"), *(len(r[1]) for r in rows))
    head = f"{'ID':<{w1}}  {'Status':<{w2}}  Title"
    sep = f"{'-'*w1}  {'-'*w2}  {'-'*5}"
    body = [f"{a:<{w1}}  {b:<{w2}}  {c}" for a, b, c in rows]
    return "\n".join([head, sep, *body])


def format_status(completed: bool) -> str:
    return "done" if completed else "todo"
