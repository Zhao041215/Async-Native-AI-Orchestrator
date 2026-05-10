import argparse, json, sys
from pathlib import Path
from task_manager.cli.formatter import print_tasks, ok, err

DB = Path("tasks.json")

def load_tasks():
    if not DB.exists(): return []
    try: return json.loads(DB.read_text())
    except Exception: return []

def save_tasks(tasks): DB.write_text(json.dumps(tasks, indent=2))

def next_id(tasks): return max([t["id"] for t in tasks] or [0]) + 1

def get_task(tasks, task_id):
    for t in tasks:
        if t["id"] == task_id: return t
    return None

def build_parser():
    p = argparse.ArgumentParser(prog="task-manager", description="Simple task manager CLI")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="Add a new task"); a.add_argument("title")
    l = sub.add_parser("list", help="List tasks"); l.add_argument("--status", choices=["pending", "completed"])
    u = sub.add_parser("update", help="Update a task title"); u.add_argument("id", type=int); u.add_argument("title")
    c = sub.add_parser("complete", help="Mark a task as completed"); c.add_argument("id", type=int)
    d = sub.add_parser("delete", help="Delete a task"); d.add_argument("id", type=int)
    return p

def main(argv=None):
    args = build_parser().parse_args(argv)
    tasks = load_tasks()
    if args.cmd == "add":
        t = {"id": next_id(tasks), "title": args.title, "status": "pending"}
        tasks.append(t); save_tasks(tasks); ok(f'Added task #{t["id"]}: {t["title"]}')
    elif args.cmd == "list":
        items = [t for t in tasks if not args.status or t["status"] == args.status]
        print_tasks(items)
    elif args.cmd == "update":
        t = get_task(tasks, args.id)
        if not t: return err(f"Task #{args.id} not found", 1)
        t["title"] = args.title; save_tasks(tasks); ok(f"Updated task #{args.id}")
    elif args.cmd == "complete":
        t = get_task(tasks, args.id)
        if not t: return err(f"Task #{args.id} not found", 1)
        t["status"] = "completed"; save_tasks(tasks); ok(f"Completed task #{args.id}")
    elif args.cmd == "delete":
        if not get_task(tasks, args.id): return err(f"Task #{args.id} not found", 1)
        save_tasks([t for t in tasks if t["id"] != args.id]); ok(f"Deleted task #{args.id}")
    return 0

if __name__ == "__main__": sys.exit(main())
