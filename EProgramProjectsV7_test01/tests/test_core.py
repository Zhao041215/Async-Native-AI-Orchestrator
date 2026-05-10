import importlib
import pytest


def _import_core():
    candidates = [
        "calculator.core",
        "core",
        "app.core",
        "src.core",
        "calculator",
    ]
    for name in candidates:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        if all(hasattr(mod, fn) for fn in ("add", "subtract", "multiply", "divide")):
            return mod
    raise AssertionError("Could not find core module with add/subtract/multiply/divide")


core = _import_core()


def test_add():
    assert core.add(2, 3) == 5
    assert core.add(-1, 1) == 0


def test_subtract():
    assert core.subtract(10, 4) == 6
    assert core.subtract(0, 5) == -5


def test_multiply():
    assert core.multiply(6, 7) == 42
    assert core.multiply(-3, 2) == -6


def test_divide():
    assert core.divide(8, 2) == 4
    assert core.divide(7, 2) == 3.5


def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        core.divide(1, 0)
