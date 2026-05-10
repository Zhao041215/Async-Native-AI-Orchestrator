import argparse

TASKS = [
    {"id": 1, "title": "Write docs", "status": "todo", "priority": 2},
    {"id": 2, "title": "Ship release", "status": "done", "priority": 1},
]


def msg(kind, text):
    print(f"[{kind.upper()}] {text}")


def render(tasks):
    if not tasks:
        msg("info", "No tasks found. Try `list --status todo` or add a new task.")
        return
    hdr = f"{'ID':<4} {'Status':<8} {'Prio':<5} Title"
    print(hdr)
    print("-" * len(hdr))
    for t in tasks:
        print(f"{t['id']:<4} {t['status']:<8} {t['priority']:<5} {t['title']}")


def get_tasks(status=None, sort="id"):
    items = TASKS[:]
    if status and status != "all":
        items = [t for t in items if t["status"] == status]
    key = {"id": "id", "title": "title", "priority": "priority", "status": "status"}[sort]
    return sorted(items, key=lambda x: x[key])


def build_parser():
    p = argparse.ArgumentParser(
        prog="task-manager",
        description="Simple task manager CLI with readable task output.",
        epilog="Examples: task-manager list --status todo --sort priority"
    )
    sub = p.add_subparsers(dest="cmd")

    l = sub.add_parser("list", help="Show tasks in a compact table")
    l.add_argument("--status", choices=["all", "todo", "done"], default="all", help="Filter by task status")
    l.add_argument("--sort", choices=["id", "title", "priority", "status"], default="id", help="Sort displayed tasks")

    a = sub.add_parser("add", help="Add a new task")
    a.add_argument("title", help="Task title")
    a.add_argument("--priority", type=int, choices=[1, 2, 3], default=2, help="1=high, 3=low")

    c = sub.add_parser("complete", help="Mark a task as done")
    c.add_argument("id", type=int, help="Task id")

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "list":
        render(get_tasks(args.status, args.sort))
    elif args.cmd == "add":
        msg("success", f"Added task: '{args.title}' (priority {args.priority})")
    elif args.cmd == "complete":
        found = next((t for t in TASKS if t["id"] == args.id), None)
        msg("success", f"Completed task #{args.id}: {found['title']}") if found else msg("error", f"Task #{args.id} not found")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
