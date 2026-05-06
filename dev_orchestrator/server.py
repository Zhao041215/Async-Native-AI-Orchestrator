from __future__ import annotations

import json
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dev_orchestrator.config import LLMConfig, load_config, update_config
from dev_orchestrator.healthcheck import build_system_check
from dev_orchestrator.llm_client import OpenAICompatibleClient, list_provider_profiles
from dev_orchestrator.v2.acceptance import run_v2_acceptance_check
from dev_orchestrator.v2.service import V2Orchestrator


class Application:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.config = load_config(root_dir)
        self.v2 = V2Orchestrator(config=self.config)
        self.static_dir = root_dir / "dev_orchestrator" / "static"

    def reload(self) -> None:
        self.config = load_config(self.root_dir)
        self.v2 = V2Orchestrator(config=self.config)


def run_server(host: str = "127.0.0.1", port: int = 8787) -> None:
    root_dir = Path(__file__).resolve().parent.parent
    app = Application(root_dir)
    handler = build_handler(app)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"Hosted V2 Control Plane running at http://{host}:{port}")
    server.serve_forever()


def _header_session(app: Application, handler: BaseHTTPRequestHandler) -> dict:
    tenant_id = handler.headers.get(app.config.identity.tenant_header) or app.config.identity.default_tenant
    user_id = handler.headers.get(app.config.identity.user_header) or app.config.identity.default_user
    return app.v2.resolve_session(tenant_id=tenant_id, user_id=user_id)


def _temporary_llm_config(app: Application, payload: dict) -> LLMConfig:
    merged = asdict(app.config.llm)
    llm_payload = payload.get("llm") if isinstance(payload.get("llm"), dict) else payload
    for key, value in llm_payload.items():
        if key == "api_key" and value in {"", "***", None}:
            continue
        if key in merged:
            merged[key] = value
    if not isinstance(merged.get("extra_headers"), dict):
        merged["extra_headers"] = {}
    return LLMConfig(**merged)


def build_handler(app: Application):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HostedV2Orchestrator/2.0"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                session = _header_session(app, self)
                tenant_id = session["tenant"]["id"]
                if parsed.path == "/api/health":
                    self._write_json({"ok": True, "status": "ready", "surface": "v2-hosted"})
                    return
                if parsed.path == "/api/system-check":
                    self._write_json(build_system_check(app.root_dir, app.config.to_dict(), app.v2.storage))
                    return
                if parsed.path == "/api/acceptance-check":
                    self._write_json(run_v2_acceptance_check(app.config))
                    return
                if parsed.path == "/api/config":
                    self._write_json(app.config.to_dict())
                    return
                if parsed.path == "/api/v2/model/profiles":
                    self._write_json({"items": list_provider_profiles(), "default": "openai-chat-completions"})
                    return
                if parsed.path == "/api/v2/auth/dev-session":
                    self._write_json(app.v2.default_session())
                    return
                if parsed.path == "/api/v2/tenants/current":
                    self._write_json(app.v2.current_tenant(tenant_id=tenant_id, user_id=session["user"]["id"]))
                    return
                if parsed.path == "/api/v2/audit-events":
                    self._write_json({"items": app.v2.list_audit_events(tenant_id=tenant_id)})
                    return
                if parsed.path == "/api/v2/worker-jobs":
                    self._write_json({"items": app.v2.list_worker_jobs(tenant_id=tenant_id)})
                    return
                if parsed.path == "/api/v2/durable-jobs":
                    self._write_json({"items": app.v2.list_durable_jobs(tenant_id=tenant_id)})
                    return
                if parsed.path == "/api/v2/benchmarks/enterprise-saas":
                    self._write_json(app.v2.get_enterprise_saas_benchmark())
                    return
                if parsed.path == "/api/v2/projects":
                    self._write_json({"items": app.v2.list_projects(tenant_id=tenant_id)})
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/continuation"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_run_continuation(run_id))
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/artifacts"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_run_artifacts(run_id))
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/events"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json({"items": app.v2.get_events(run_id)})
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/agent-runs"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json({"items": app.v2.get_agent_runs(run_id)})
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/patches"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json({"items": app.v2.get_patch_sets(run_id)})
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/tests"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json({"items": app.v2.get_test_runs(run_id)})
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/release-patch"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_release_patch(run_id))
                    return
                if parsed.path.startswith("/api/v2/runs/"):
                    run_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_run(run_id))
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/work-packages"):
                    project_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_work_packages(project_id))
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/coverage"):
                    project_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_coverage(project_id))
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/quality-gates"):
                    project_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_quality_gates(project_id))
                    return
                if parsed.path.startswith("/api/v2/release-candidates/"):
                    candidate_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_release_candidate(candidate_id))
                    return
                if parsed.path.startswith("/api/v2/projects/"):
                    project_id = parsed.path.split("/")[4]
                    self._write_json(app.v2.get_project(project_id))
                    return
                if parsed.path.startswith("/api/tasks") or parsed.path.startswith("/api/changes"):
                    self._write_json({"error": "Legacy V1 APIs were removed; use /api/v2/*."}, HTTPStatus.GONE)
                    return
                if parsed.path == "/robots.txt":
                    self._write_text("User-agent: *\nDisallow:\n", "text/plain; charset=utf-8")
                    return
                self._serve_static(parsed.path)
            except KeyError as exc:
                self._write_json({"error": f"Not found: {exc}"}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                session = _header_session(app, self)
                tenant_id = session["tenant"]["id"]
                user_id = session["user"]["id"]
                if parsed.path == "/api/config":
                    update_config(app.config, payload)
                    app.reload()
                    self._write_json({"ok": True, "config": app.config.to_dict()})
                    return
                if parsed.path == "/api/v2/model/test-connection":
                    llm_config = _temporary_llm_config(app, payload)
                    result = OpenAICompatibleClient(llm_config).test_connection()
                    self._write_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST)
                    return
                if parsed.path == "/api/v2/auth/dev-session":
                    requested_tenant = payload.get("tenant_id") or payload.get("tenant") or app.config.identity.default_tenant
                    requested_user = payload.get("user_id") or payload.get("user") or app.config.identity.default_user
                    self._write_json(app.v2.resolve_session(requested_tenant, requested_user))
                    return
                if parsed.path == "/api/v2/projects":
                    project = app.v2.create_project(
                        name=payload.get("name") or payload.get("project_name") or payload.get("title") or "project",
                        title=payload.get("title", ""),
                        description=payload.get("description", ""),
                        project_path=payload.get("project_path") or None,
                        automation_mode=payload.get("automation_mode") or None,
                        target_scale=payload.get("target_scale") or None,
                        benchmark_type=payload.get("benchmark_type") or None,
                        unattended_mode=payload.get("unattended_mode") or None,
                        effective_loc_target=payload.get("effective_loc_target"),
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    self._write_json(project, HTTPStatus.CREATED)
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/requirements"):
                    project_id = parsed.path.split("/")[4]
                    result = app.v2.ingest_requirements(project_id, payload.get("text") or payload.get("description") or "")
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/blueprint/validate"):
                    project_id = parsed.path.split("/")[4]
                    result = app.v2.validate_project_blueprint(project_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/projects/") and parsed.path.endswith("/runs"):
                    project_id = parsed.path.split("/")[4]
                    result = app.v2.start_project_run(project_id, tenant_id=tenant_id, user_id=user_id)
                    self._write_json(result, HTTPStatus.ACCEPTED)
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/resume"):
                    run_id = parsed.path.split("/")[4]
                    result = app.v2.resume_run(run_id)
                    self._write_json(result, HTTPStatus.ACCEPTED)
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/pause"):
                    run_id = parsed.path.split("/")[4]
                    result = app.v2.pause_run(run_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/runs/") and parsed.path.endswith("/cancel"):
                    run_id = parsed.path.split("/")[4]
                    result = app.v2.cancel_run(run_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/repair-tasks/") and parsed.path.endswith("/run"):
                    repair_id = parsed.path.split("/")[4]
                    result = app.v2.run_repair_task(repair_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/release-candidates/") and parsed.path.endswith("/apply"):
                    candidate_id = parsed.path.split("/")[4]
                    result = app.v2.apply_release_candidate(candidate_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/release-candidates/") and parsed.path.endswith("/auto-apply"):
                    candidate_id = parsed.path.split("/")[4]
                    result = app.v2.auto_apply_release_candidate(candidate_id)
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/v2/release-candidates/") and parsed.path.endswith("/rollback"):
                    candidate_id = parsed.path.split("/")[4]
                    result = app.v2.rollback_release_candidate(candidate_id)
                    self._write_json(result)
                    return
                if parsed.path == "/api/v2/approvals":
                    result = app.v2.approve(
                        project_id=payload.get("project_id", ""),
                        approver=payload.get("approver", session["user"]["display_name"]),
                        note=payload.get("note", ""),
                        tenant_id=tenant_id,
                        user_id=user_id,
                    )
                    self._write_json(result)
                    return
                if parsed.path.startswith("/api/tasks") or parsed.path.startswith("/api/changes"):
                    self._write_json({"error": "Legacy V1 APIs were removed; use /api/v2/*."}, HTTPStatus.GONE)
                    return
                self._write_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except KeyError as exc:
                self._write_json({"error": f"Not found: {exc}"}, HTTPStatus.NOT_FOUND)
            except ValueError as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._write_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def log_message(self, format: str, *args) -> None:
            return

        def _read_json(self) -> dict:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                return {}
            body = self.rfile.read(content_length).decode("utf-8")
            return json.loads(body)

        def _serve_static(self, path: str) -> None:
            if path in {"", "/"}:
                path = "/index.html"
            if path == "/app.js":
                content_type = "application/javascript; charset=utf-8"
            elif path == "/styles.css":
                content_type = "text/css; charset=utf-8"
            elif path.endswith(".html"):
                content_type = "text/html; charset=utf-8"
            else:
                self._write_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            file_path = (app.static_dir / path.lstrip("/")).resolve()
            if not file_path.exists():
                self._write_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
                return
            self._write_bytes(file_path.read_bytes(), content_type)

        def _write_json(self, payload: dict, code: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self._write_bytes(body, "application/json; charset=utf-8", code)

        def _write_text(self, payload: str, content_type: str, code: int = HTTPStatus.OK) -> None:
            self._write_bytes(payload.encode("utf-8"), content_type, code)

        def _write_bytes(self, payload: bytes, content_type: str, code: int = HTTPStatus.OK) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler
