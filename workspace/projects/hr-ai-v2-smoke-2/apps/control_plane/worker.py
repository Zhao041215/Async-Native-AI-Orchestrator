from __future__ import annotations

import time


def main() -> None:
    print("v2 worker ready; queue backend is configured by V2_REDIS_URL")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
