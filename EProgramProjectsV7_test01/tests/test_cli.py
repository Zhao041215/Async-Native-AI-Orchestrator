import importlib
import io
import contextlib
import pytest


def _import_cli():
    candidates = [
        "calculator.cli",
        "cli",
        "app.cli",
        "main",
    ]
    for name in candidates:
        try:
            mod = importlib.import_module(name)
        except Exception:
            continue
        if hasattr(mod, "main"):
            return mod
    raise AssertionError("Could not find CLI module with main()")


cli = _import_cli()


def _run_cli(args):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            result = cli.main(args)
            if isinstance(result, int):
                code = result
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
    return code, buf_out.getvalue(), buf_err.getvalue()


@pytest.mark.parametrize(
    "args, expected",
    [
        (["add", "2", "3"], "5"),
        (["subtract", "9", "4"], "5"),
        (["multiply", "6", "7"], "42"),
        (["divide", "8", "2"], "4"),
    ],
)
def test_cli_operations(args, expected):
    code, out, err = _run_cli(args)
    text = (out + err).strip()
    assert code == 0
    assert expected in text


def test_cli_divide_by_zero():
    code, out, err = _run_cli(["divide", "1", "0"])
    text = (out + err).lower()
    assert code != 0 or "zero" in text
    assert "zero" in text or "division" in text


@pytest.mark.parametrize(
    "args",
    [
        ["nope", "1", "2"],
        [],
        ["add", "x", "2"],
        ["add", "1"],
    ],
)
def test_cli_invalid_args(args):
    code, out, err = _run_cli(args)
    text = (out + err).lower()
    assert code != 0
    assert any(word in text for word in ["usage", "error", "invalid", "required"])
