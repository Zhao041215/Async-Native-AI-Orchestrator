import argparse
import sys

try:
    from .core import add, subtract, multiply, divide
except Exception:  # pragma: no cover
    from calculator.core import add, subtract, multiply, divide

OPS = {
    "add": add,
    "subtract": subtract,
    "multiply": multiply,
    "divide": divide,
}


def build_parser():
    p = argparse.ArgumentParser(prog="calculator", description="Simple CLI calculator")
    p.add_argument("operation", choices=sorted(OPS))
    p.add_argument("operands", nargs=2, type=float)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    a, b = args.operands
    try:
        result = OPS[args.operation](a, b)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
