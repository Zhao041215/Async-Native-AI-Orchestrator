from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from repository import Repository


repository = Repository()


class Handler(BaseHTTPRequestHandler):
    def _write_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._write_json(repository.health())
            return
        if self.path == "/api/projects":
            self._write_json({"items": repository.list_projects()})
            return
        if self.path == "/api/tasks":
            self._write_json({"items": repository.list_tasks()})
            return
        self._write_json({"error": "Not found"}, 404)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 9000), Handler)
    print("API running at http://127.0.0.1:9000")
    server.serve_forever()


if __name__ == "__main__":
    main()
