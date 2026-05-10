from typing import Iterable, Mapping, Any


def _line(ch: str = "-", n: int = 40) -> str:
    return ch * n


def success(msg: str) -> str:
    return f"[OK] {msg}"


def error(msg: str) -> str:
    return f"[ERROR] {msg}"


def info(msg: str) -> str:
    return f"[INFO] {msg}"


def format_status(done: bool) -> str:
    return "done" if done else "pending"


def format_task_list(tasks: Iterable[Mapping[str, Any]]) -> str:
    items = list(tasks)
    if not items:
        return info("No tasks found.")
    rows = []
    for i, t in enumerate(items, 1):
        title = str(t.get("title", "Untitled"))
        status = format_status(bool(t.get("completed", t.get("done", False))))
        task_id = t.get("id", i)
        rows.append(f"{task_id:>3} | {status:<7} | {title}")
    head = " ID | STATUS  | TITLE"
    return "\n".join([head, _line("-", len(head)), *rows])


def format_task_detail(task: Mapping[str, Any]) -> str:
    title = str(task.get("title", "Untitled"))
    task_id = task.get("id", "-")
    status = format_status(bool(task.get("completed", task.get("done", False))))
    desc = str(task.get("description", "")).strip() or "-"
    return "\n".join([
        f"Task #{task_id}",
        _line(),
        f"Title: {title}",
        f"Status: {status}",
        f"Description: {desc}",
    ])


def format_help(app_name: str = "task") -> str:
    return "\n".join([
        f"{app_name} - simple task manager",
        _line(),
        "Usage:",
        f"  {app_name} add <title>",
        f"  {app_name} list",
        f"  {app_name} done <id>",
        f"  {app_name} delete <id>",
        "",
        "Examples:",
        f"  {app_name} add \"Buy milk\"",
        f"  {app_name} done 2",
    ])
