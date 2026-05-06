from __future__ import annotations

import json
from pathlib import Path


DOCKER_COMPOSE = """services:
  api:
    build:
      context: .
      dockerfile: infra/Dockerfile
    command: python -m apps.control_plane.api
    environment:
      V2_DATABASE_URL: postgresql://orchestrator:orchestrator@postgres:5432/orchestrator
      V2_REDIS_URL: redis://redis:6379/0
      V2_SECRET_SOURCE: environment
    ports:
      - "8788:8788"
    depends_on:
      - postgres
      - redis

  worker:
    build:
      context: .
      dockerfile: infra/Dockerfile
    command: python -m apps.control_plane.worker
    environment:
      V2_DATABASE_URL: postgresql://orchestrator:orchestrator@postgres:5432/orchestrator
      V2_REDIS_URL: redis://redis:6379/0
      V2_SECRET_SOURCE: environment
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: orchestrator
      POSTGRES_PASSWORD: orchestrator
      POSTGRES_DB: orchestrator
    volumes:
      - v2-postgres:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine

volumes:
  v2-postgres:
"""


DOCKERFILE = """FROM python:3.12-slim

WORKDIR /app
COPY . /app
ENV PYTHONUNBUFFERED=1
EXPOSE 8788
CMD ["python", "-m", "apps.control_plane.api"]
"""


FASTAPI_APP = '''from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"ok": True, "service": "hosted-v2-control-plane", "tenant_required": True})
            return
        if self.path == "/ready":
            self._json({"ok": True, "queue": "redis-compatible", "database": "postgres-compatible"})
            return
        self._json({"error": "not found"}, 404)

    def log_message(self, format: str, *args) -> None:
        return

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8788), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
'''


WORKER_APP = '''from __future__ import annotations

import time


def main() -> None:
    print("hosted v2 worker ready; queue backend is configured by V2_REDIS_URL")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
'''


ENV_EXAMPLE = """V2_DATABASE_URL=postgresql://orchestrator:orchestrator@localhost:5432/orchestrator
V2_REDIS_URL=redis://localhost:6379/0
V2_REQUIRE_TENANT=true
V2_REQUIRE_IDENTITY=true
OPENAI_API_KEY=
OPENAI_API_BASE=
OPENAI_MODEL=
"""


def ensure_production_skeleton(project_root: Path) -> dict:
    project_root = Path(project_root)
    files = {
        "docker-compose.v2.yml": DOCKER_COMPOSE,
        "infra/Dockerfile": DOCKERFILE,
        "infra/v2.env.example": ENV_EXAMPLE,
        "apps/control_plane/__init__.py": "",
        "apps/control_plane/api.py": FASTAPI_APP,
        "apps/control_plane/worker.py": WORKER_APP,
    }
    written: list[str] = []
    for relative, content in files.items():
        path = project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8", errors="ignore") != content:
            path.write_text(content, encoding="utf-8")
        written.append(relative)

    manifest = {
        "schema_version": "2.0.0",
        "kind": "production-skeleton",
        "runtime": "docker-compose",
        "control_plane": "Hosted V2 HTTP service skeleton",
        "queue": "Redis-compatible worker job boundary",
        "database": "Postgres-compatible tenant and audit boundary",
        "secret_policy": "environment-only; no plaintext API keys in config files",
        "identity_policy": "tenant and identity are required for hosted operation",
        "files": written,
    }
    manifest_path = project_root / ".agent" / "v2" / "production-skeleton.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True), encoding="utf-8")
    return manifest
