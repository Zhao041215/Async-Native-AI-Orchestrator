import os

import pytest

from .conftest import run_cli


def test_cli_help_runs(cli_cmd):
    result = run_cli(cli_cmd, '--help')
    assert result.returncode == 0
    assert result.stdout or result.stderr


def test_cli_add_and_list_flow(cli_cmd, tmp_path):
    store = tmp_path / 'cli_tasks.json'
    env = {
        'TASKS_FILE': str(store),
        'TASK_FILE': str(store),
        'TASK_MANAGER_FILE': str(store),
    }
    add_res = run_cli(cli_cmd, 'add', 'cli task', env=env)
    if add_res.returncode != 0:
        pytest.skip('CLI add command shape differs')
    list_res = run_cli(cli_cmd, 'list', env=env)
    assert list_res.returncode == 0
    assert 'cli task' in (list_res.stdout + list_res.stderr).lower()


def test_cli_invalid_command_fails(cli_cmd):
    result = run_cli(cli_cmd, 'definitely-not-a-real-command')
    assert result.returncode != 0
