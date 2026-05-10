import importlib
import json
import os
import pathlib
import subprocess
import sys

import pytest


CANDIDATE_MODULES = [
    'task_manager', 'taskmanager', 'app', 'main', 'cli', 'tasks'
]


def try_import(name):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def load_project_module():
    for name in CANDIDATE_MODULES:
        mod = try_import(name)
        if mod:
            return mod
    pytest.skip('No supported project module found')


@pytest.fixture
def project_module():
    return load_project_module()


@pytest.fixture
def manager_class(project_module):
    for attr in ['TaskManager', 'Manager', 'TodoApp']:
        cls = getattr(project_module, attr, None)
        if cls:
            return cls
    pytest.skip('No manager class found')


@pytest.fixture
def temp_store(tmp_path):
    return tmp_path / 'tasks.json'


def detect_cli_command():
    root = pathlib.Path.cwd()
    for path in [root / 'task_manager.py', root / 'main.py', root / 'cli.py']:
        if path.exists():
            return [sys.executable, str(path)]
    for mod in ['task_manager', 'main', 'cli']:
        if try_import(mod):
            return [sys.executable, '-m', mod]
    return None


@pytest.fixture
def cli_cmd():
    cmd = detect_cli_command()
    if not cmd:
        pytest.skip('No CLI entrypoint found')
    return cmd


def run_cli(cmd, *args, env=None):
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(cmd + list(args), capture_output=True, text=True, env=full_env)


def read_json(path):
    return json.loads(path.read_text()) if path.exists() and path.read_text().strip() else None
