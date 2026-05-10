import argparse
from task_manager.service import TaskManager


manager = TaskManager()


def _print_task(t):
    mark = 'x' if t.completed else ' '
    print(f'[{mark}] {t.id}: {t.title}')


def main():
    p = argparse.ArgumentParser(prog='task')
    sub = p.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('add'); a.add_argument('title')
    sub.add_parser('list')
    c = sub.add_parser('complete'); c.add_argument('id', type=int)
    d = sub.add_parser('delete'); d.add_argument('id', type=int)
    u = sub.add_parser('update'); u.add_argument('id', type=int); u.add_argument('title')
    args = p.parse_args()

    try:
        if args.cmd == 'add':
            _print_task(manager.add(args.title))
        elif args.cmd == 'list':
            for t in manager.list(): _print_task(t)
        elif args.cmd == 'complete':
            _print_task(manager.complete(args.id))
        elif args.cmd == 'delete':
            _print_task(manager.delete(args.id))
        elif args.cmd == 'update':
            _print_task(manager.update(args.id, args.title))
    except ValueError as e:
        print(f'error: {e}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
