from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from dev_orchestrator.v2.models import slugify, utc_now


class V2Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS v2_projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    project_path TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'local-workspace',
                    created_by TEXT NOT NULL DEFAULT 'local-operator',
                    owner_user_id TEXT NOT NULL DEFAULT 'local-operator',
                    visibility TEXT NOT NULL DEFAULT 'tenant',
                    approval_state TEXT NOT NULL DEFAULT 'not_requested',
                    automation_mode TEXT NOT NULL DEFAULT 'supervised_auto',
                    target_scale TEXT NOT NULL DEFAULT 'medium',
                    benchmark_type TEXT NOT NULL DEFAULT 'enterprise_saas',
                    unattended_mode TEXT NOT NULL DEFAULT 'off',
                    effective_loc_target INTEGER NOT NULL DEFAULT 100000,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_requirements (
                    project_id TEXT PRIMARY KEY,
                    raw_text TEXT NOT NULL,
                    atoms_json TEXT NOT NULL,
                    contracts_json TEXT NOT NULL,
                    subsystems_json TEXT NOT NULL,
                    work_packages_json TEXT NOT NULL,
                    decisions_json TEXT NOT NULL,
                    coverage_json TEXT NOT NULL,
                    findings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(id)
                );

                CREATE TABLE IF NOT EXISTS v2_runs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL DEFAULT 'local-workspace',
                    created_by TEXT NOT NULL DEFAULT 'local-operator',
                    worker_job_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    chief_summary TEXT NOT NULL,
                    decomposition_score INTEGER NOT NULL DEFAULT 0,
                    autonomy_level INTEGER NOT NULL DEFAULT 2,
                    repair_round_count INTEGER NOT NULL DEFAULT 0,
                    quality_gate_history_json TEXT NOT NULL DEFAULT '[]',
                    continuation_state_json TEXT NOT NULL DEFAULT '{}',
                    durable_queue_state_json TEXT NOT NULL DEFAULT '{}',
                    artifact_manifest_path TEXT NOT NULL DEFAULT '',
                    context_snapshot_id TEXT NOT NULL DEFAULT '',
                    effective_loc_metrics_json TEXT NOT NULL DEFAULT '{}',
                    rollback_manifest_path TEXT NOT NULL DEFAULT '',
                    benchmark_score INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(id)
                );

                CREATE TABLE IF NOT EXISTS v2_events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_validation_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_repair_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    finding_code TEXT NOT NULL,
                    assigned_role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(id),
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_release_candidates (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    gate_score INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(id),
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_agent_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    work_package_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    model_calls INTEGER NOT NULL,
                    tool_calls INTEGER NOT NULL,
                    error TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_worktrees (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    work_package_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    branch TEXT NOT NULL,
                    base_sha TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    cleaned_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_patch_sets (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    work_package_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    patch_path TEXT NOT NULL,
                    files_changed_json TEXT NOT NULL,
                    diff_summary TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_test_runs (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    repair_round INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_integration_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    work_package_id TEXT NOT NULL,
                    patch_set_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_tenants (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_users (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    handle TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, handle),
                    FOREIGN KEY(tenant_id) REFERENCES v2_tenants(id)
                );

                CREATE TABLE IF NOT EXISTS v2_approvals (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    release_candidate_id TEXT NOT NULL,
                    approver_user_id TEXT NOT NULL,
                    approver TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES v2_projects(id)
                );

                CREATE TABLE IF NOT EXISTS v2_audit_events (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_worker_jobs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    queue TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    locked_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES v2_runs(id)
                );

                CREATE TABLE IF NOT EXISTS v2_durable_jobs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    role TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    resume_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    UNIQUE(resume_key)
                );

                CREATE TABLE IF NOT EXISTS v2_artifacts (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_context_snapshots (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_code_index (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    area TEXT NOT NULL,
                    language TEXT NOT NULL,
                    symbol_count INTEGER NOT NULL,
                    line_count INTEGER NOT NULL,
                    keywords_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_benchmark_runs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    benchmark_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    effective_loc INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v2_release_rollbacks (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    release_candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reverse_patch_path TEXT NOT NULL,
                    manifest_path TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                """
            )
            self._ensure_schema(conn)

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        self._ensure_columns(
            conn,
            "v2_projects",
            {
                "tenant_id": "TEXT NOT NULL DEFAULT 'local-workspace'",
                "created_by": "TEXT NOT NULL DEFAULT 'local-operator'",
                "owner_user_id": "TEXT NOT NULL DEFAULT 'local-operator'",
                "visibility": "TEXT NOT NULL DEFAULT 'tenant'",
                "approval_state": "TEXT NOT NULL DEFAULT 'not_requested'",
                "automation_mode": "TEXT NOT NULL DEFAULT 'supervised_auto'",
                "target_scale": "TEXT NOT NULL DEFAULT 'medium'",
                "benchmark_type": "TEXT NOT NULL DEFAULT 'enterprise_saas'",
                "unattended_mode": "TEXT NOT NULL DEFAULT 'off'",
                "effective_loc_target": "INTEGER NOT NULL DEFAULT 100000",
            },
        )
        self._ensure_columns(
            conn,
            "v2_runs",
            {
                "tenant_id": "TEXT NOT NULL DEFAULT 'local-workspace'",
                "created_by": "TEXT NOT NULL DEFAULT 'local-operator'",
                "worker_job_id": "TEXT NOT NULL DEFAULT ''",
                "decomposition_score": "INTEGER NOT NULL DEFAULT 0",
                "autonomy_level": "INTEGER NOT NULL DEFAULT 2",
                "repair_round_count": "INTEGER NOT NULL DEFAULT 0",
                "quality_gate_history_json": "TEXT NOT NULL DEFAULT '[]'",
                "continuation_state_json": "TEXT NOT NULL DEFAULT '{}'",
                "durable_queue_state_json": "TEXT NOT NULL DEFAULT '{}'",
                "artifact_manifest_path": "TEXT NOT NULL DEFAULT ''",
                "context_snapshot_id": "TEXT NOT NULL DEFAULT ''",
                "effective_loc_metrics_json": "TEXT NOT NULL DEFAULT '{}'",
                "rollback_manifest_path": "TEXT NOT NULL DEFAULT ''",
                "benchmark_score": "INTEGER NOT NULL DEFAULT 0",
            },
        )

    def _ensure_columns(self, conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def get_or_create_tenant(self, slug: str, name: str | None = None) -> dict:
        safe_slug = slugify(slug or "local-workspace")
        tenant_id = safe_slug
        now = utc_now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_tenants WHERE slug = ?", (safe_slug,)).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO v2_tenants (id, slug, name, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (tenant_id, safe_slug, name or safe_slug, "active", now, now),
                )
                row = conn.execute("SELECT * FROM v2_tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row)

    def get_tenant(self, tenant_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_tenants WHERE id = ?", (tenant_id,)).fetchone()
        if row is None:
            raise KeyError(tenant_id)
        return dict(row)

    def get_or_create_user(self, handle: str, tenant_id: str, display_name: str | None = None, role: str = "operator") -> dict:
        safe_handle = slugify(handle or "local-operator")
        user_id = f"{tenant_id}:{safe_handle}"
        now = utc_now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM v2_users WHERE tenant_id = ? AND handle = ?",
                (tenant_id, safe_handle),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO v2_users (id, tenant_id, handle, display_name, role, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, tenant_id, safe_handle, display_name or safe_handle, role, "active", now, now),
                )
                row = conn.execute("SELECT * FROM v2_users WHERE id = ?", (user_id,)).fetchone()
        return dict(row)

    def get_user(self, user_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise KeyError(user_id)
        return dict(row)

    def create_project(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_projects (
                    id, name, title, description, project_path, tenant_id, created_by,
                    owner_user_id, visibility, approval_state, automation_mode, target_scale,
                    benchmark_type, unattended_mode, effective_loc_target, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["name"],
                    payload["title"],
                    payload["description"],
                    payload["project_path"],
                    payload.get("tenant_id", "local-workspace"),
                    payload.get("created_by", "local-operator"),
                    payload.get("owner_user_id", payload.get("created_by", "local-operator")),
                    payload.get("visibility", "tenant"),
                    payload.get("approval_state", "not_requested"),
                    payload.get("automation_mode", "supervised_auto"),
                    payload.get("target_scale", "medium"),
                    payload.get("benchmark_type", "enterprise_saas"),
                    payload.get("unattended_mode", "off"),
                    int(payload.get("effective_loc_target", 100000)),
                    payload.get("status", "draft"),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
        return self.get_project(payload["id"])

    def get_project(self, project_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(row)

    def list_projects(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM v2_projects ORDER BY updated_at DESC, created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_projects_for_tenant(self, tenant_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_projects
                WHERE tenant_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_project(self, project_id: str, **fields: object) -> dict:
        if not fields:
            return self.get_project(project_id)
        fields["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [project_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_projects SET {columns} WHERE id = ?", values)
        return self.get_project(project_id)

    def save_requirement_bundle(self, project_id: str, bundle: dict) -> dict:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_requirements (
                    project_id, raw_text, atoms_json, contracts_json, subsystems_json,
                    work_packages_json, decisions_json, coverage_json, findings_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id)
                DO UPDATE SET raw_text = excluded.raw_text,
                              atoms_json = excluded.atoms_json,
                              contracts_json = excluded.contracts_json,
                              subsystems_json = excluded.subsystems_json,
                              work_packages_json = excluded.work_packages_json,
                              decisions_json = excluded.decisions_json,
                              coverage_json = excluded.coverage_json,
                              findings_json = excluded.findings_json,
                              updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    bundle.get("raw_text", ""),
                    json.dumps(bundle.get("atoms", []), ensure_ascii=True),
                    json.dumps(bundle.get("contracts", []), ensure_ascii=True),
                    json.dumps(bundle.get("subsystems", []), ensure_ascii=True),
                    json.dumps(bundle.get("work_packages", []), ensure_ascii=True),
                    json.dumps(bundle.get("decisions", []), ensure_ascii=True),
                    json.dumps(bundle.get("coverage", {}), ensure_ascii=True),
                    json.dumps(bundle.get("findings", []), ensure_ascii=True),
                    now,
                ),
            )
        return self.get_requirement_bundle(project_id)

    def get_requirement_bundle(self, project_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_requirements WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            return {
                "project_id": project_id,
                "raw_text": "",
                "atoms": [],
                "contracts": [],
                "subsystems": [],
                "work_packages": [],
                "decisions": [],
                "coverage": {
                    "must_total": 0,
                    "must_covered": 0,
                    "must_coverage_percent": 0,
                    "uncovered_must": [],
                },
                "findings": [],
                "updated_at": "",
            }
        return {
            "project_id": row["project_id"],
            "raw_text": row["raw_text"],
            "atoms": json.loads(row["atoms_json"]),
            "contracts": json.loads(row["contracts_json"]),
            "subsystems": json.loads(row["subsystems_json"]),
            "work_packages": json.loads(row["work_packages_json"]),
            "decisions": json.loads(row["decisions_json"]),
            "coverage": json.loads(row["coverage_json"]),
            "findings": json.loads(row["findings_json"]),
            "updated_at": row["updated_at"],
        }

    def create_run(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_runs (
                    id, project_id, tenant_id, created_by, worker_job_id,
                    status, chief_summary, decomposition_score, autonomy_level, repair_round_count,
                    quality_gate_history_json, continuation_state_json, durable_queue_state_json,
                    artifact_manifest_path, context_snapshot_id, effective_loc_metrics_json,
                    rollback_manifest_path, benchmark_score, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["project_id"],
                    payload.get("tenant_id", "local-workspace"),
                    payload.get("created_by", "local-operator"),
                    payload.get("worker_job_id", ""),
                    payload["status"],
                    payload.get("chief_summary", ""),
                    int(payload.get("decomposition_score", 0)),
                    int(payload.get("autonomy_level", 2)),
                    int(payload.get("repair_round_count", 0)),
                    json.dumps(payload.get("quality_gate_history", []), ensure_ascii=True),
                    json.dumps(payload.get("continuation_state", {}), ensure_ascii=True),
                    json.dumps(payload.get("durable_queue_state", {}), ensure_ascii=True),
                    payload.get("artifact_manifest_path", ""),
                    payload.get("context_snapshot_id", ""),
                    json.dumps(payload.get("effective_loc_metrics", {}), ensure_ascii=True),
                    payload.get("rollback_manifest_path", ""),
                    int(payload.get("benchmark_score", 0)),
                    payload["created_at"],
                    payload["updated_at"],
                ),
            )
        return self.get_run(payload["id"])

    def update_run(self, run_id: str, **fields: object) -> dict:
        if not fields:
            return self.get_run(run_id)
        if "quality_gate_history" in fields:
            fields["quality_gate_history_json"] = json.dumps(fields.pop("quality_gate_history"), ensure_ascii=True)
        if "continuation_state" in fields:
            fields["continuation_state_json"] = json.dumps(fields.pop("continuation_state"), ensure_ascii=True)
        if "durable_queue_state" in fields:
            fields["durable_queue_state_json"] = json.dumps(fields.pop("durable_queue_state"), ensure_ascii=True)
        if "effective_loc_metrics" in fields:
            fields["effective_loc_metrics_json"] = json.dumps(fields.pop("effective_loc_metrics"), ensure_ascii=True)
        fields["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [run_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_runs SET {columns} WHERE id = ?", values)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_row_to_dict(row)

    def list_project_runs(self, project_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_runs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [self._run_row_to_dict(row) for row in rows]

    def _run_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["quality_gate_history"] = json.loads(item.pop("quality_gate_history_json", "[]") or "[]")
        item["continuation_state"] = json.loads(item.pop("continuation_state_json", "{}") or "{}")
        item["durable_queue_state"] = json.loads(item.pop("durable_queue_state_json", "{}") or "{}")
        item["effective_loc_metrics"] = json.loads(item.pop("effective_loc_metrics_json", "{}") or "{}")
        return item

    def save_approval(self, payload: dict) -> dict:
        item = dict(payload)
        item["id"] = item.get("id") or str(uuid.uuid4())
        item["created_at"] = item.get("created_at") or utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_approvals (
                    id, tenant_id, project_id, release_candidate_id, approver_user_id,
                    approver, decision, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["project_id"],
                    item["release_candidate_id"],
                    item["approver_user_id"],
                    item.get("approver", ""),
                    item.get("decision", "approved"),
                    item.get("note", ""),
                    item["created_at"],
                ),
            )
        return item

    def list_project_approvals(self, project_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_approvals WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_audit_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        message: str,
        payload: dict | None = None,
    ) -> dict:
        item = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "message": message,
            "payload": payload or {},
            "created_at": utc_now(),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_audit_events (
                    id, tenant_id, user_id, action, resource_type, resource_id,
                    message, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["user_id"],
                    item["action"],
                    item["resource_type"],
                    item["resource_id"],
                    item["message"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["created_at"],
                ),
            )
        return item

    def list_audit_events(self, tenant_id: str, limit: int = 200) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_audit_events
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, int(limit)),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            items.append(item)
        return items

    def create_worker_job(self, payload: dict) -> dict:
        now = utc_now()
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload["tenant_id"],
            "run_id": payload["run_id"],
            "queue": payload.get("queue", "v2-runs"),
            "worker_id": payload.get("worker_id", ""),
            "status": payload.get("status", "queued"),
            "attempts": int(payload.get("attempts", 0)),
            "payload": payload.get("payload", {}),
            "created_at": payload.get("created_at", now),
            "updated_at": payload.get("updated_at", now),
            "locked_at": payload.get("locked_at", ""),
            "finished_at": payload.get("finished_at", ""),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_worker_jobs (
                    id, tenant_id, run_id, queue, worker_id, status, attempts,
                    payload_json, created_at, updated_at, locked_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["run_id"],
                    item["queue"],
                    item["worker_id"],
                    item["status"],
                    item["attempts"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["created_at"],
                    item["updated_at"],
                    item["locked_at"],
                    item["finished_at"],
                ),
            )
        return item

    def update_worker_job(self, job_id: str, **fields: object) -> dict:
        if "payload" in fields:
            fields["payload_json"] = json.dumps(fields.pop("payload"), ensure_ascii=True)
        fields["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_worker_jobs SET {columns} WHERE id = ?", values)
        return self.get_worker_job(job_id)

    def get_worker_job(self, job_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_worker_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._worker_job_row_to_dict(row)

    def list_worker_jobs(self, tenant_id: str, limit: int = 200) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_worker_jobs
                WHERE tenant_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (tenant_id, int(limit)),
            ).fetchall()
        return [self._worker_job_row_to_dict(row) for row in rows]

    def _worker_job_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def add_event(self, run_id: str, level: str, source: str, message: str, payload: dict | None = None) -> dict:
        import uuid

        event = {
            "id": str(uuid.uuid4()),
            "run_id": run_id,
            "ts": utc_now(),
            "level": level,
            "source": source,
            "message": message,
            "payload": payload or {},
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_events (id, run_id, ts, level, source, message, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["run_id"],
                    event["ts"],
                    event["level"],
                    event["source"],
                    event["message"],
                    json.dumps(event["payload"], ensure_ascii=True),
                ),
            )
        return event

    def list_events(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_events WHERE run_id = ? ORDER BY ts ASC",
                (run_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "run_id": row["run_id"],
                "ts": row["ts"],
                "level": row["level"],
                "source": row["source"],
                "message": row["message"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_validation(self, run_id: str, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_validation_runs (id, run_id, gate, status, score, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    run_id,
                    payload["gate"],
                    payload["status"],
                    int(payload.get("score", 0)),
                    json.dumps(payload, ensure_ascii=True),
                    payload.get("created_at") or utc_now(),
                ),
            )
        return payload

    def list_validations(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM v2_validation_runs WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_repair_task(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_repair_tasks (
                    id, project_id, run_id, finding_code, assigned_role, status,
                    reason, created_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["project_id"],
                    payload["run_id"],
                    payload["finding_code"],
                    payload["assigned_role"],
                    payload["status"],
                    payload["reason"],
                    payload["created_at"],
                    payload.get("resolved_at", ""),
                ),
            )
        return payload

    def get_repair_task(self, repair_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_repair_tasks WHERE id = ?", (repair_id,)).fetchone()
        if row is None:
            raise KeyError(repair_id)
        return dict(row)

    def update_repair_task(self, repair_id: str, **fields: object) -> dict:
        if not fields:
            return self.get_repair_task(repair_id)
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [repair_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_repair_tasks SET {columns} WHERE id = ?", values)
        return self.get_repair_task(repair_id)

    def list_project_repairs(self, project_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_repair_tasks WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_release_candidate(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_release_candidates (
                    id, project_id, run_id, status, decision, gate_score, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["project_id"],
                    payload["run_id"],
                    payload["status"],
                    payload["decision"],
                    int(payload.get("gate_score", 0)),
                    json.dumps(payload, ensure_ascii=True),
                    payload["created_at"],
                ),
            )
        return payload

    def update_release_candidate(self, candidate_id: str, **fields: object) -> dict:
        candidate = self.get_release_candidate(candidate_id)
        candidate.update(fields)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE v2_release_candidates
                SET status = ?, decision = ?, gate_score = ?, payload_json = ?
                WHERE id = ?
                """,
                (
                    candidate.get("status", "ready"),
                    candidate.get("decision", "NO_GO"),
                    int(candidate.get("gate_score", 0)),
                    json.dumps(candidate, ensure_ascii=True),
                    candidate_id,
                ),
            )
        return self.get_release_candidate(candidate_id)

    def get_release_candidate(self, candidate_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM v2_release_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            raise KeyError(candidate_id)
        return json.loads(row["payload_json"])

    def latest_release_candidate_for_project(self, project_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload_json FROM v2_release_candidates
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_agent_run(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_agent_runs (
                    id, run_id, work_package_id, role, attempt, status, worktree_path,
                    branch, summary, model_calls, tool_calls, error, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    summary = excluded.summary,
                    model_calls = excluded.model_calls,
                    tool_calls = excluded.tool_calls,
                    error = excluded.error,
                    finished_at = excluded.finished_at
                """,
                (
                    payload["id"],
                    payload["run_id"],
                    payload["work_package_id"],
                    payload["role"],
                    int(payload.get("attempt", 1)),
                    payload["status"],
                    payload.get("worktree_path", ""),
                    payload.get("branch", ""),
                    payload.get("summary", ""),
                    int(payload.get("model_calls", 0)),
                    int(payload.get("tool_calls", 0)),
                    payload.get("error", ""),
                    payload.get("started_at", utc_now()),
                    payload.get("finished_at", ""),
                ),
            )
        return payload

    def list_agent_runs(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_agent_runs WHERE run_id = ? ORDER BY started_at ASC, work_package_id ASC, attempt ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_worktree(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_worktrees (
                    id, run_id, work_package_id, role, attempt, path, branch,
                    base_sha, status, created_at, cleaned_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["run_id"],
                    payload["work_package_id"],
                    payload["role"],
                    int(payload.get("attempt", 1)),
                    payload["path"],
                    payload.get("branch", ""),
                    payload.get("base_sha", ""),
                    payload.get("status", "created"),
                    payload.get("created_at", utc_now()),
                    payload.get("cleaned_at", ""),
                ),
            )
        return payload

    def update_worktree(self, worktree_id: str, **fields: object) -> dict:
        if not fields:
            raise ValueError("No fields to update.")
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [worktree_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_worktrees SET {columns} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM v2_worktrees WHERE id = ?", (worktree_id,)).fetchone()
        if row is None:
            raise KeyError(worktree_id)
        return dict(row)

    def list_worktrees(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_worktrees WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_patch_set(self, payload: dict) -> dict:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_patch_sets (
                    id, run_id, work_package_id, role, attempt, status, patch_path,
                    files_changed_json, diff_summary, error, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["id"],
                    payload["run_id"],
                    payload["work_package_id"],
                    payload["role"],
                    int(payload.get("attempt", 1)),
                    payload["status"],
                    payload.get("patch_path", ""),
                    json.dumps(payload.get("files_changed", []), ensure_ascii=True),
                    payload.get("diff_summary", ""),
                    payload.get("error", ""),
                    payload.get("created_at", utc_now()),
                ),
            )
        return payload

    def update_patch_set(self, patch_set_id: str, **fields: object) -> dict:
        if "files_changed" in fields:
            fields["files_changed_json"] = json.dumps(fields.pop("files_changed"), ensure_ascii=True)
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [patch_set_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_patch_sets SET {columns} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM v2_patch_sets WHERE id = ?", (patch_set_id,)).fetchone()
        if row is None:
            raise KeyError(patch_set_id)
        return self._patch_row_to_dict(row)

    def list_patch_sets(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_patch_sets WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [self._patch_row_to_dict(row) for row in rows]

    def _patch_row_to_dict(self, row: sqlite3.Row) -> dict:
        payload = dict(row)
        payload["files_changed"] = json.loads(payload.pop("files_changed_json"))
        return payload

    def save_test_run(self, run_id: str, payload: dict) -> dict:
        import uuid

        item = dict(payload)
        item["id"] = item.get("id") or str(uuid.uuid4())
        item["run_id"] = run_id
        item["created_at"] = item.get("created_at") or utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_test_runs (
                    id, run_id, command, cwd, kind, status, exit_code,
                    stdout, stderr, repair_round, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    run_id,
                    item.get("command", ""),
                    item.get("cwd", "."),
                    item.get("kind", "custom"),
                    item.get("status", "failed"),
                    int(item.get("exit_code", 1)),
                    item.get("stdout", ""),
                    item.get("stderr", ""),
                    int(item.get("repair_round", 0)),
                    item["created_at"],
                ),
            )
        return item

    def list_test_runs(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_test_runs WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_integration_step(self, payload: dict) -> dict:
        import uuid

        item = dict(payload)
        item["id"] = item.get("id") or str(uuid.uuid4())
        item["created_at"] = item.get("created_at") or utc_now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_integration_steps (
                    id, run_id, step_type, status, work_package_id, patch_set_id,
                    message, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["run_id"],
                    item.get("step_type", "integration"),
                    item.get("status", "unknown"),
                    item.get("work_package_id", ""),
                    item.get("patch_set_id", ""),
                    item.get("message", ""),
                    json.dumps(item.get("payload", {}), ensure_ascii=True),
                    item["created_at"],
                ),
            )
        return item

    def list_integration_steps(self, run_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM v2_integration_steps WHERE run_id = ? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            items.append(item)
        return items

    def enqueue_durable_job(self, payload: dict) -> dict:
        now = utc_now()
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload["tenant_id"],
            "run_id": payload.get("run_id", ""),
            "project_id": payload.get("project_id", ""),
            "kind": payload.get("kind", "run"),
            "role": payload.get("role", "chief"),
            "priority": int(payload.get("priority", 100)),
            "status": payload.get("status", "queued"),
            "worker_id": payload.get("worker_id", ""),
            "lease_until": payload.get("lease_until", ""),
            "heartbeat_at": payload.get("heartbeat_at", ""),
            "attempts": int(payload.get("attempts", 0)),
            "max_attempts": int(payload.get("max_attempts", 3)),
            "resume_key": payload.get("resume_key") or payload.get("id") or str(uuid.uuid4()),
            "payload": payload.get("payload", {}),
            "error": payload.get("error", ""),
            "created_at": payload.get("created_at", now),
            "updated_at": payload.get("updated_at", now),
            "started_at": payload.get("started_at", ""),
            "finished_at": payload.get("finished_at", ""),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_durable_jobs (
                    id, tenant_id, run_id, project_id, kind, role, priority, status,
                    worker_id, lease_until, heartbeat_at, attempts, max_attempts,
                    resume_key, payload_json, error, created_at, updated_at, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resume_key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    priority = excluded.priority,
                    status = excluded.status,
                    worker_id = '',
                    lease_until = '',
                    heartbeat_at = '',
                    error = '',
                    finished_at = '',
                    updated_at = excluded.updated_at
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["run_id"],
                    item["project_id"],
                    item["kind"],
                    item["role"],
                    item["priority"],
                    item["status"],
                    item["worker_id"],
                    item["lease_until"],
                    item["heartbeat_at"],
                    item["attempts"],
                    item["max_attempts"],
                    item["resume_key"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["error"],
                    item["created_at"],
                    item["updated_at"],
                    item["started_at"],
                    item["finished_at"],
                ),
            )
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE resume_key = ?", (item["resume_key"],)).fetchone()
        return self._durable_job_row_to_dict(row)

    def get_durable_job(self, job_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._durable_job_row_to_dict(row)

    def lease_durable_job_by_id(self, job_id: str, worker_id: str, lease_until: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] == "leased" and row["worker_id"] == worker_id:
                return self._durable_job_row_to_dict(row)
            if row["status"] not in {"queued", "retry"}:
                return self._durable_job_row_to_dict(row)
            attempts = int(row["attempts"]) + 1
            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE v2_durable_jobs
                SET status = 'leased', worker_id = ?, lease_until = ?, heartbeat_at = ?,
                    attempts = ?, started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    updated_at = ?
                WHERE id = ? AND status IN ('queued', 'retry')
                """,
                (worker_id, lease_until, now, attempts, now, now, job_id),
            )
            if cursor.rowcount == 0:
                row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
                return self._durable_job_row_to_dict(row)
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._durable_job_row_to_dict(row)

    def lease_durable_job(
        self,
        tenant_id: str,
        worker_id: str,
        lease_until: str,
        kinds: list[str] | None = None,
        roles: list[str] | None = None,
    ) -> dict | None:
        kinds = kinds or []
        roles = roles or []
        with self._lock, self._connect() as conn:
            clauses = ["tenant_id = ?", "status IN ('queued', 'retry')"]
            values: list[object] = [tenant_id]
            if kinds:
                placeholders = ",".join("?" for _ in kinds)
                clauses.append(f"kind IN ({placeholders})")
                values.extend(kinds)
            if roles:
                placeholders = ",".join("?" for _ in roles)
                clauses.append(f"role IN ({placeholders})")
                values.extend(roles)
            row = conn.execute(
                f"""
                SELECT * FROM v2_durable_jobs
                WHERE {" AND ".join(clauses)}
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                values,
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"]) + 1
            now = utc_now()
            cursor = conn.execute(
                """
                UPDATE v2_durable_jobs
                SET status = 'leased', worker_id = ?, lease_until = ?, heartbeat_at = ?,
                    attempts = ?, started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    updated_at = ?
                WHERE id = ? AND status IN ('queued', 'retry')
                """,
                (worker_id, lease_until, now, attempts, now, now, row["id"]),
            )
            if cursor.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._durable_job_row_to_dict(row)

    def heartbeat_durable_job(self, job_id: str, worker_id: str, lease_until: str | None = None) -> dict:
        fields: dict[str, object] = {"heartbeat_at": utc_now(), "worker_id": worker_id}
        if lease_until is not None:
            fields["lease_until"] = lease_until
        return self.update_durable_job(job_id, **fields)

    def update_durable_job(self, job_id: str, **fields: object) -> dict:
        if not fields:
            return self.get_durable_job(job_id)
        if "payload" in fields:
            fields["payload_json"] = json.dumps(fields.pop("payload"), ensure_ascii=True)
        fields["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_durable_jobs SET {columns} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._durable_job_row_to_dict(row)

    def finish_durable_job(self, job_id: str, status: str = "completed", error: str = "") -> dict:
        return self.update_durable_job(job_id, status=status, error=error, lease_until="", finished_at=utc_now())

    def fail_durable_job(self, job_id: str, error: str, retryable: bool = True) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            if retryable and attempts < max_attempts:
                status = "retry"
                finished_at = ""
            else:
                status = "dead_letter" if retryable else "failed"
                finished_at = utc_now()
            now = utc_now()
            conn.execute(
                """
                UPDATE v2_durable_jobs
                SET status = ?, error = ?, worker_id = '', lease_until = '',
                    heartbeat_at = '', updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (status, error, now, finished_at, job_id),
            )
            row = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._durable_job_row_to_dict(row)

    def pause_pending_durable_jobs_for_run(self, run_id: str, reason: str = "pause_requested") -> list[dict]:
        now = utc_now()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_durable_jobs
                WHERE run_id = ? AND status IN ('queued', 'retry')
                """,
                (run_id,),
            ).fetchall()
            updated: list[dict] = []
            for row in rows:
                conn.execute(
                    """
                    UPDATE v2_durable_jobs
                    SET status = 'paused', error = ?, worker_id = '', lease_until = '',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (reason, now, row["id"]),
                )
                refreshed = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (row["id"],)).fetchone()
                updated.append(self._durable_job_row_to_dict(refreshed))
        return updated

    def cancel_durable_jobs_for_run(self, run_id: str, status: str = "cancelled", reason: str = "") -> list[dict]:
        now = utc_now()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_durable_jobs
                WHERE run_id = ? AND status NOT IN ('completed', 'failed', 'cancelled', 'dead_letter')
                """,
                (run_id,),
            ).fetchall()
            updated: list[dict] = []
            for row in rows:
                conn.execute(
                    """
                    UPDATE v2_durable_jobs
                    SET status = ?, error = ?, worker_id = '', lease_until = '',
                        updated_at = ?, finished_at = CASE WHEN ? = 'cancelled' THEN ? ELSE finished_at END
                    WHERE id = ?
                    """,
                    (status, reason, now, status, now, row["id"]),
                )
                refreshed = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (row["id"],)).fetchone()
                updated.append(self._durable_job_row_to_dict(refreshed))
        return updated

    def requeue_expired_jobs(self, now_iso: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM v2_durable_jobs
                WHERE status = 'leased' AND lease_until != '' AND lease_until < ?
                """,
                (now_iso,),
            ).fetchall()
            updated: list[dict] = []
            for row in rows:
                next_status = "dead_letter" if int(row["attempts"]) >= int(row["max_attempts"]) else "retry"
                conn.execute(
                    """
                    UPDATE v2_durable_jobs
                    SET status = ?, worker_id = '', lease_until = '', error = ?,
                        updated_at = ?, finished_at = CASE WHEN ? = 'dead_letter' THEN ? ELSE finished_at END
                    WHERE id = ?
                    """,
                    (next_status, "lease_timeout", now_iso, next_status, now_iso, row["id"]),
                )
                refreshed = conn.execute("SELECT * FROM v2_durable_jobs WHERE id = ?", (row["id"],)).fetchone()
                updated.append(self._durable_job_row_to_dict(refreshed))
        return updated

    def list_durable_jobs(self, tenant_id: str | None = None, run_id: str | None = None, limit: int = 200) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            values.append(tenant_id)
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM v2_durable_jobs {where} ORDER BY created_at DESC LIMIT ?",
                [*values, int(limit)],
            ).fetchall()
        return [self._durable_job_row_to_dict(row) for row in rows]

    def _durable_job_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def save_artifact(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload.get("tenant_id", ""),
            "project_id": payload.get("project_id", ""),
            "run_id": payload.get("run_id", ""),
            "kind": payload.get("kind", "artifact"),
            "path": payload["path"],
            "sha256": payload.get("sha256", ""),
            "size_bytes": int(payload.get("size_bytes", 0)),
            "payload": payload.get("payload", {}),
            "created_at": payload.get("created_at", utc_now()),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_artifacts (
                    id, tenant_id, project_id, run_id, kind, path, sha256,
                    size_bytes, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["project_id"],
                    item["run_id"],
                    item["kind"],
                    item["path"],
                    item["sha256"],
                    item["size_bytes"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["created_at"],
                ),
            )
        return item

    def list_artifacts(self, run_id: str | None = None, project_id: str | None = None, kind: str | None = None) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM v2_artifacts {where} ORDER BY created_at ASC", values).fetchall()
        return [self._artifact_row_to_dict(row) for row in rows]

    def _artifact_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def save_context_snapshot(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload["tenant_id"],
            "project_id": payload["project_id"],
            "run_id": payload.get("run_id", ""),
            "summary": payload.get("summary", ""),
            "payload": payload.get("payload", {}),
            "artifact_id": payload.get("artifact_id", ""),
            "created_at": payload.get("created_at", utc_now()),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_context_snapshots (
                    id, tenant_id, project_id, run_id, summary, payload_json, artifact_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["project_id"],
                    item["run_id"],
                    item["summary"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["artifact_id"],
                    item["created_at"],
                ),
            )
        return item

    def get_context_snapshot(self, snapshot_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_context_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(snapshot_id)
        return self._context_snapshot_row_to_dict(row)

    def latest_context_snapshot(self, project_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM v2_context_snapshots
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return self._context_snapshot_row_to_dict(row) if row else None

    def _context_snapshot_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def save_code_index_entries(self, entries: list[dict]) -> list[dict]:
        if not entries:
            return []
        with self._lock, self._connect() as conn:
            for payload in entries:
                item = {
                    "id": payload.get("id") or str(uuid.uuid4()),
                    "tenant_id": payload["tenant_id"],
                    "project_id": payload["project_id"],
                    "run_id": payload.get("run_id", ""),
                    "snapshot_id": payload.get("snapshot_id", ""),
                    "path": payload["path"],
                    "area": payload.get("area", ""),
                    "language": payload.get("language", ""),
                    "symbol_count": int(payload.get("symbol_count", 0)),
                    "line_count": int(payload.get("line_count", 0)),
                    "keywords": payload.get("keywords", []),
                    "summary": payload.get("summary", ""),
                    "created_at": payload.get("created_at", utc_now()),
                }
                payload.update(item)
                conn.execute(
                    """
                    INSERT INTO v2_code_index (
                        id, tenant_id, project_id, run_id, snapshot_id, path, area,
                        language, symbol_count, line_count, keywords_json, summary, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        item["tenant_id"],
                        item["project_id"],
                        item["run_id"],
                        item["snapshot_id"],
                        item["path"],
                        item["area"],
                        item["language"],
                        item["symbol_count"],
                        item["line_count"],
                        json.dumps(item["keywords"], ensure_ascii=True),
                        item["summary"],
                        item["created_at"],
                    ),
                )
        return entries

    def list_code_index(self, project_id: str, run_id: str | None = None, limit: int = 500) -> list[dict]:
        if run_id:
            query = "SELECT * FROM v2_code_index WHERE project_id = ? AND run_id = ? ORDER BY path ASC LIMIT ?"
            values: list[object] = [project_id, run_id, int(limit)]
        else:
            query = "SELECT * FROM v2_code_index WHERE project_id = ? ORDER BY created_at DESC, path ASC LIMIT ?"
            values = [project_id, int(limit)]
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, values).fetchall()
        return [self._code_index_row_to_dict(row) for row in rows]

    def _code_index_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["keywords"] = json.loads(item.pop("keywords_json") or "[]")
        return item

    def save_benchmark_run(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload["tenant_id"],
            "project_id": payload["project_id"],
            "run_id": payload.get("run_id", ""),
            "benchmark_type": payload.get("benchmark_type", "enterprise_saas"),
            "status": payload.get("status", "pending"),
            "score": int(payload.get("score", 0)),
            "effective_loc": int(payload.get("effective_loc", 0)),
            "payload": payload.get("payload", {}),
            "created_at": payload.get("created_at", utc_now()),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_benchmark_runs (
                    id, tenant_id, project_id, run_id, benchmark_type, status,
                    score, effective_loc, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["project_id"],
                    item["run_id"],
                    item["benchmark_type"],
                    item["status"],
                    item["score"],
                    item["effective_loc"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["created_at"],
                ),
            )
        return item

    def list_benchmark_runs(self, project_id: str | None = None, benchmark_type: str | None = None) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if benchmark_type:
            clauses.append("benchmark_type = ?")
            values.append(benchmark_type)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM v2_benchmark_runs {where} ORDER BY created_at DESC", values).fetchall()
        return [self._benchmark_row_to_dict(row) for row in rows]

    def _benchmark_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item

    def save_release_rollback(self, payload: dict) -> dict:
        item = {
            "id": payload.get("id") or str(uuid.uuid4()),
            "tenant_id": payload["tenant_id"],
            "project_id": payload["project_id"],
            "run_id": payload["run_id"],
            "release_candidate_id": payload["release_candidate_id"],
            "status": payload.get("status", "prepared"),
            "reverse_patch_path": payload.get("reverse_patch_path", ""),
            "manifest_path": payload.get("manifest_path", ""),
            "reason": payload.get("reason", ""),
            "payload": payload.get("payload", {}),
            "created_at": payload.get("created_at", utc_now()),
            "applied_at": payload.get("applied_at", ""),
        }
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO v2_release_rollbacks (
                    id, tenant_id, project_id, run_id, release_candidate_id, status,
                    reverse_patch_path, manifest_path, reason, payload_json, created_at, applied_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["tenant_id"],
                    item["project_id"],
                    item["run_id"],
                    item["release_candidate_id"],
                    item["status"],
                    item["reverse_patch_path"],
                    item["manifest_path"],
                    item["reason"],
                    json.dumps(item["payload"], ensure_ascii=True),
                    item["created_at"],
                    item["applied_at"],
                ),
            )
        return item

    def update_release_rollback(self, rollback_id: str, **fields: object) -> dict:
        if "payload" in fields:
            fields["payload_json"] = json.dumps(fields.pop("payload"), ensure_ascii=True)
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [rollback_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE v2_release_rollbacks SET {columns} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM v2_release_rollbacks WHERE id = ?", (rollback_id,)).fetchone()
        if row is None:
            raise KeyError(rollback_id)
        return self._rollback_row_to_dict(row)

    def get_release_rollback(self, rollback_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM v2_release_rollbacks WHERE id = ?", (rollback_id,)).fetchone()
        if row is None:
            raise KeyError(rollback_id)
        return self._rollback_row_to_dict(row)

    def latest_release_rollback_for_candidate(self, candidate_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM v2_release_rollbacks
                WHERE release_candidate_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
        return self._rollback_row_to_dict(row) if row else None

    def list_release_rollbacks(self, project_id: str | None = None, run_id: str | None = None) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if project_id:
            clauses.append("project_id = ?")
            values.append(project_id)
        if run_id:
            clauses.append("run_id = ?")
            values.append(run_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM v2_release_rollbacks {where} ORDER BY created_at DESC", values).fetchall()
        return [self._rollback_row_to_dict(row) for row in rows]

    def _rollback_row_to_dict(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        return item
