import inspect

import pytest


def _make_manager(manager_class, temp_store):
    try:
        return manager_class(str(temp_store))
    except Exception:
        try:
            return manager_class(storage_path=str(temp_store))
        except Exception:
            return manager_class()


def _find_method(obj, names):
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn
    return None


def test_manager_exposes_core_operations(manager_class, temp_store):
    mgr = _make_manager(manager_class, temp_store)
    assert _find_method(mgr, ['add_task', 'add'])
    assert _find_method(mgr, ['list_tasks', 'list', 'get_tasks'])


def test_add_task_and_list(manager_class, temp_store):
    mgr = _make_manager(manager_class, temp_store)
    add = _find_method(mgr, ['add_task', 'add'])
    list_fn = _find_method(mgr, ['list_tasks', 'list', 'get_tasks'])
    add('buy milk')
    tasks = list_fn()
    assert tasks is not None
    text = str(tasks).lower()
    assert 'milk' in text


def test_complete_or_toggle_task_when_supported(manager_class, temp_store):
    mgr = _make_manager(manager_class, temp_store)
    add = _find_method(mgr, ['add_task', 'add'])
    done = _find_method(mgr, ['complete_task', 'complete', 'mark_done', 'toggle_task'])
    if not done:
        pytest.skip('No completion method found')
    add('finish report')
    try:
        done(0)
    except Exception:
        done(1)
    list_fn = _find_method(mgr, ['list_tasks', 'list', 'get_tasks'])
    assert any(tok in str(list_fn()).lower() for tok in ['done', 'complete', 'true', 'finished'])


def test_rejects_empty_task_when_supported(manager_class, temp_store):
    mgr = _make_manager(manager_class, temp_store)
    add = _find_method(mgr, ['add_task', 'add'])
    with pytest.raises(Exception):
        add('')
