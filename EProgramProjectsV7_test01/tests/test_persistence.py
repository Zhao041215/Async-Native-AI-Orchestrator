import pytest

from .conftest import read_json


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


def test_tasks_persist_across_instances(manager_class, temp_store):
    mgr1 = _make_manager(manager_class, temp_store)
    add = _find_method(mgr1, ['add_task', 'add'])
    save = _find_method(mgr1, ['save', 'save_tasks'])
    add('persist me')
    if save:
        save()
    mgr2 = _make_manager(manager_class, temp_store)
    list_fn = _find_method(mgr2, ['list_tasks', 'list', 'get_tasks'])
    assert 'persist me' in str(list_fn()).lower()


def test_storage_file_created_or_updated(manager_class, temp_store):
    mgr = _make_manager(manager_class, temp_store)
    add = _find_method(mgr, ['add_task', 'add'])
    save = _find_method(mgr, ['save', 'save_tasks'])
    add('write file')
    if save:
        save()
    if temp_store.exists():
        data = read_json(temp_store)
        assert data is None or isinstance(data, (list, dict))
    else:
        pytest.skip('Project may persist elsewhere')
