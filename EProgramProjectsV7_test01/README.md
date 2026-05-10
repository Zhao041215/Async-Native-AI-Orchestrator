# task_manager_v10

Simple Python CLI task manager.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

Run the CLI with:

```bash
python -m task_manager_v10 --help
```

## CLI Usage

Typical pattern:

```bash
python -m task_manager_v10 <command> [options]
```

Common commands:

- `add` - create a task
- `list` - show tasks
- `done` - mark a task complete
- `delete` - remove a task

## Examples

```bash
python -m task_manager_v10 add "Buy milk"
python -m task_manager_v10 add "Write report" --priority high
python -m task_manager_v10 list
python -m task_manager_v10 done 1
python -m task_manager_v10 delete 2
```

If your entrypoint is a script instead of module execution, adapt examples to:

```bash
python cli.py list
```

## Storage Behavior

The app stores tasks locally on disk for a simple single-user workflow.

Expected behavior:

- tasks persist between runs
- updates overwrite previous stored state
- no server or external database is required
- storage is intended for local development and personal usage

## Architecture Notes

High-level flow:

1. CLI parses a command and arguments
2. command calls task-management logic
3. task data is loaded from local storage
4. changes are written back to storage
5. formatted output is printed to the terminal

This keeps the project lightweight and easy to extend.

## Development

### Run locally

```bash
python -m task_manager_v10 --help
```

### Testing

Run the test suite with the command used by the project, commonly:

```bash
pytest -q
```

If tests use the standard library only:

```bash
python -m unittest
```

## Contributing

1. create a branch
2. make a small change
3. run formatting/tests
4. open a pull request

Recommended checks:

```bash
pytest -q
python -m task_manager_v10 --help
```
