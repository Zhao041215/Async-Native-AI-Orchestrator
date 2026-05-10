from __future__ import annotations

import copy
from time import time as time_time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dev_orchestrator.v6.kernel import build_state_transition_event
from dev_orchestrator.v6.models import DEFAULT_TENANT, new_id, normalize_database_url, sha256_bytes, stable_json, utc_now
from dev_orchestrator.v6.role_aliases import canonical_worker_role
from dev_orchestrator.v6.role_aliases import canonical_worker_role

try:
    from sqlalchemy import (
        Boolean,
        Column,
        DateTime,
        Float,
        Integer,
        MetaData,
        func,
        String,
        Table,
        Text,
        and_,
        delete,
        create_engine,
        insert,
        select,
        text,
        update,
    )
    from sqlalchemy.dialects.postgresql import JSONB, insert as pg_insert
    from sqlalchemy.engine import Engine
except Exception:  # pragma: no cover - lets pure unit tests run without optional deps.
    Boolean = Column = DateTime = Float = Integer = MetaData = String = Table = Text = None  # type: ignore[assignment]
    and_ = create_engine = delete = insert = select = text = update = None  # type: ignore[assignment]
    JSONB = None  # type: ignore[assignment]
    pg_insert = None  # type: ignore[assignment]
    Engine = object  # type: ignore[assignment,misc]


def _json_type() -> Any:
    if JSONB is None:
        raise RuntimeError("SQLAlchemy/psycopg dependencies are not installed")
    return JSONB


def _metadata() -> Any:
    if MetaData is None:
        raise RuntimeError("SQLAlchemy dependencies are not installed")
    metadata = MetaData()
    Table(
        "tenants",
        metadata,
        Column("id", String(120), primary_key=True),
        Column("name", String(255), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "users",
        metadata,
        Column("id", String(120), primary_key=True),
        Column("tenant_id", String(120), nullable=False),
        Column("username", String(255), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "projects",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(120), nullable=False),
        Column("name", String(255), nullable=False),
        Column("title", String(500), nullable=False),
        Column("description", Text, nullable=False),
        Column("project_path", Text, nullable=False),
        Column("config", _json_type(), nullable=False),
        Column("status", String(60), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "runs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(120), nullable=False),
        Column("project_id", String(36), nullable=False),
        Column("status", String(80), nullable=False),
        Column("checkpoint", String(120), nullable=False),
        Column("continuation", _json_type(), nullable=False),
        Column("metadata", _json_type(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "work_packages",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("wave_id", String(36), nullable=False),
        Column("wave_key", String(60), nullable=False),
        Column("package_key", String(120), nullable=False),
        Column("role", String(60), nullable=False),
        Column("domain", String(120), nullable=False),
        Column("status", String(80), nullable=False),
        Column("payload", _json_type(), nullable=False),
        Column("result", _json_type(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "run_waves",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("wave_key", String(60), nullable=False),
        Column("sequence", Integer, nullable=False),
        Column("status", String(80), nullable=False),
        Column("payload", _json_type(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "durable_jobs",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(120), nullable=False),
        Column("run_id", String(36), nullable=True),
        Column("job_type", String(80), nullable=False),
        Column("role", String(60), nullable=False),
        Column("status", String(80), nullable=False),
        Column("resume_key", String(500), nullable=False, unique=True),
        Column("work_package_id", String(36), nullable=True),
        Column("wave_id", String(36), nullable=True),
        Column("payload", _json_type(), nullable=False),
        Column("result", _json_type(), nullable=False),
        Column("ai_budget", _json_type(), nullable=False, default={}),
        Column("model_tier", String(80), nullable=False, default=""),
        Column("degraded", Boolean, nullable=False, default=False),
        Column("subsystem", String(160), nullable=False, default=""),
        Column("depends_on", _json_type(), nullable=False, default=[]),
        Column("allowed_paths", _json_type(), nullable=False, default=[]),
        Column("attempts", Integer, nullable=False),
        Column("max_attempts", Integer, nullable=False),
        Column("worker_id", String(255), nullable=False),
        Column("lease_until", DateTime(timezone=True), nullable=True),
        Column("heartbeat_at", DateTime(timezone=True), nullable=True),
        Column("last_error", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    for table_name in (
        "agent_runs",
        "patch_sets",
        "integration_steps",
        "test_runs",
        "quality_reports",
        "artifacts",
        "release_candidates",
        "rollbacks",
        "events",
        "context_snapshots",
        "code_index",
    ):
        Table(
            table_name,
            metadata,
            Column("id", String(36), primary_key=True),
            Column("tenant_id", String(120), nullable=False),
            Column("project_id", String(36), nullable=True),
            Column("run_id", String(36), nullable=True),
            Column("kind", String(120), nullable=False),
            Column("path", Text, nullable=False),
            Column("sha256", String(64), nullable=False),
            Column("size", Integer, nullable=False),
            Column("payload", _json_type(), nullable=False),
            Column("metadata", _json_type(), nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
        )
    Table(
        "event_counters",
        metadata,
        Column("aggregate_type", String(32), primary_key=True),
        Column("aggregate_id", String(120), primary_key=True),
        Column("sequence", Integer, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "provider_health",
        metadata,
        Column("provider", String(500), primary_key=True),
        Column("status", String(80), nullable=False),
        Column("success_count", Integer, nullable=False),
        Column("failure_count", Integer, nullable=False),
        Column("failure_streak", Integer, nullable=False),
        Column("last_error_kind", String(120), nullable=False),
        Column("last_error", Text, nullable=False),
        Column("last_event_at", Float, nullable=False),
        Column("retry_after_seconds", Integer, nullable=False),
        Column("circuit_open_until", Float, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )
    Table(
        "ai_call_slots",
        metadata,
        Column("id", String(36), primary_key=True),
        Column("tenant_id", String(120), nullable=False),
        Column("run_id", String(36), nullable=False),
        Column("provider", String(500), nullable=False),
        Column("agent_run_id", String(36), nullable=False),
        Column("task_kind", String(120), nullable=False),
        Column("status", String(60), nullable=False),
        Column("acquired_at", DateTime(timezone=True), nullable=False),
        Column("lease_until", DateTime(timezone=True), nullable=False),
        Column("released_at", DateTime(timezone=True), nullable=True),
    )
    return metadata


class StoreError(RuntimeError):
    pass


class V6Store:
    def bootstrap(self) -> None:
        raise NotImplementedError

    def get_or_create_tenant(self, tenant_id: str = DEFAULT_TENANT) -> dict[str, Any]:
        raise NotImplementedError

    def create_project(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_projects(self, tenant_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def delete_project(self, project_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def delete_projects(self, project_ids: list[str]) -> dict[str, Any]:
        raise NotImplementedError

    def create_run(self, tenant_id: str, project_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        raise NotImplementedError

    def upsert_wave(self, run_id: str, wave: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_waves(self, run_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def upsert_work_package(self, run_id: str, wave_id: str, package: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def get_work_package(self, package_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def list_work_packages(self, run_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def update_work_package(self, package_id: str, **fields: Any) -> dict[str, Any]:
        raise NotImplementedError

    def enqueue_job(self, tenant_id: str, job: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def claim_job(self, tenant_id: str, role: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        raise NotImplementedError

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        raise NotImplementedError

    def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        raise NotImplementedError

    def finish_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool = True) -> dict[str, Any]:
        raise NotImplementedError

    def requeue_expired_jobs(self, tenant_id: str) -> int:
        raise NotImplementedError

    def list_jobs(self, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def normalize_roles(self) -> dict[str, Any]:
        raise NotImplementedError

    def add_artifact(self, tenant_id: str, project_id: str | None, artifact: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def add_event(self, tenant_id: str, project_id: str | None, run_id: str | None, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def list_events(self, run_id: str | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    def record_provider_success(self, provider: str) -> dict[str, Any]:
        raise NotImplementedError

    def record_provider_failure(self, provider: str, error: str, classification: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def provider_health_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError

    def acquire_ai_slot(
        self,
        *,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def release_ai_slot(self, slot_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def ai_slot_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def purge_retired_generation_records(self) -> dict[str, Any]:
        raise NotImplementedError


class PostgresV6Store(V6Store):
    def __init__(self, database_url: str):
        if not database_url:
            raise StoreError("V6_DATABASE_URL is required")
        if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise StoreError("V6 requires a Postgres database URL")
        self.database_url = normalize_database_url(database_url)
        if create_engine is None or pg_insert is None:
            raise StoreError("SQLAlchemy/psycopg dependencies are not installed")
        self.engine: Engine = create_engine(self.database_url, pool_pre_ping=True)
        self.metadata = _metadata()
        self.tables = self.metadata.tables

    def bootstrap(self) -> None:
        self.metadata.create_all(self.engine)
        self._migrate()
        self.normalize_roles()
        self.get_or_create_tenant(DEFAULT_TENANT)

    def _migrate(self) -> None:
        if text is None:
            return
        statements = (
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS ai_budget JSONB NOT NULL DEFAULT '{}'::jsonb",
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS model_tier VARCHAR(80) NOT NULL DEFAULT ''",
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS degraded BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS subsystem VARCHAR(160) NOT NULL DEFAULT ''",
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS depends_on JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE durable_jobs ADD COLUMN IF NOT EXISTS allowed_paths JSONB NOT NULL DEFAULT '[]'::jsonb",
            "ALTER TABLE provider_health ADD COLUMN IF NOT EXISTS circuit_open_until DOUBLE PRECISION NOT NULL DEFAULT 0",
        )
        with self.engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))

    def _row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        return self._clean(dict(row._mapping))

    def _clean(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    def _run_identity(self, run_id: str | None) -> dict[str, Any]:
        if not run_id:
            return {"tenant_id": DEFAULT_TENANT, "project_id": None}
        table = self.tables["runs"]
        with self.engine.begin() as conn:
            row = conn.execute(select(table.c.tenant_id, table.c.project_id).where(table.c.id == run_id)).first()
        if not row:
            return {"tenant_id": DEFAULT_TENANT, "project_id": None}
        return {"tenant_id": row[0] or DEFAULT_TENANT, "project_id": row[1]}

    def _event_envelope(
        self,
        *,
        tenant_id: str,
        project_id: str | None,
        run_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        conn=None,
    ) -> dict[str, Any]:
        event_metadata = dict(metadata or {})
        event_metadata.setdefault("schema_version", "6.1")
        event_metadata.setdefault("event_type", event_type)
        aggregate_type = "run" if run_id else ("project" if project_id else "tenant")
        aggregate_id = run_id or project_id or tenant_id
        event_metadata.setdefault("aggregate_type", aggregate_type)
        event_metadata.setdefault("aggregate_id", aggregate_id)
        event_metadata.setdefault("payload_hash", sha256_bytes(stable_json(payload).encode("utf-8")))
        event_metadata.setdefault("idempotency_key", str(payload.get("idempotency_key") or payload.get("resume_key") or payload.get("job_id") or ""))
        if "sequence" in event_metadata and event_metadata["sequence"] not in (None, ""):
            sequence = max(int(event_metadata["sequence"]) - 1, 0)
        elif conn is not None:
            table = self.tables["events"]
            statement = select(func.count()).select_from(table).where(table.c.run_id == run_id) if run_id else select(func.count()).select_from(table).where(table.c.project_id == project_id) if project_id else select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
            sequence = int(conn.execute(statement).scalar_one())
        else:
            sequence = 0
            table = self.tables["events"]
            with self.engine.begin() as inner:
                statement = select(func.count()).select_from(table).where(table.c.run_id == run_id) if run_id else select(func.count()).select_from(table).where(table.c.project_id == project_id) if project_id else select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
                sequence = int(inner.execute(statement).scalar_one())
        event_metadata["sequence"] = sequence + 1
        row = {
            "id": new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "run_id": run_id,
            "kind": event_type,
            "path": "",
            "sha256": event_metadata["payload_hash"],
            "size": len(stable_json(payload).encode("utf-8")),
            "payload": payload,
            "metadata": event_metadata,
            "created_at": utc_now(),
        }
        return row

    def get_or_create_tenant(self, tenant_id: str = DEFAULT_TENANT) -> dict[str, Any]:
        tenant_id = tenant_id or DEFAULT_TENANT
        table = self.tables["tenants"]
        with self.engine.begin() as conn:
            existing = conn.execute(select(table).where(table.c.id == tenant_id)).first()
            if existing:
                return self._row(existing) or {}
            now = utc_now()
            payload = {"id": tenant_id, "name": tenant_id, "created_at": now}
            conn.execute(insert(table).values(**payload))
            return self._clean(payload)

    def create_project(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["projects"]
        now = utc_now()
        row = {
            "id": new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "name": payload["name"],
            "title": payload.get("title") or payload["name"],
            "description": payload.get("description") or "",
            "project_path": payload.get("project_path") or "",
            "config": payload.get("config") or {},
            "status": "created",
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            conn.execute(insert(table).values(**row))
        current = self._clean(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("id"), None, "project.created", {"project_id": current.get("id", ""), "name": current.get("name", "")})
        return current

    def list_projects(self, tenant_id: str) -> list[dict[str, Any]]:
        table = self.tables["projects"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).where(table.c.tenant_id == tenant_id).order_by(table.c.created_at.desc())).all()
        return [self._row(row) or {} for row in rows]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        table = self.tables["projects"]
        with self.engine.begin() as conn:
            return self._row(conn.execute(select(table).where(table.c.id == project_id)).first())

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        table = self.tables["runs"]
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(table).where(table.c.project_id == project_id).order_by(table.c.created_at.desc())
            ).all()
        return [self._row(row) or {} for row in rows]

    def delete_project(self, project_id: str) -> dict[str, Any]:
        return self.delete_projects([project_id])

    def delete_projects(self, project_ids: list[str]) -> dict[str, Any]:
        unique_project_ids = [project_id for project_id in dict.fromkeys(project_ids) if project_id]
        if not unique_project_ids:
            return {"deleted_projects": [], "deleted_runs": 0, "deleted_jobs": 0, "deleted_artifacts": 0}
        projects_table = self.tables["projects"]
        runs_table = self.tables["runs"]
        run_ids: list[str] = []
        with self.engine.begin() as conn:
            for project_id in unique_project_ids:
                rows = conn.execute(select(runs_table.c.id).where(runs_table.c.project_id == project_id)).all()
                run_ids.extend([str(row[0]) for row in rows if row and row[0]])
            run_ids = list(dict.fromkeys(run_ids))
            if run_ids:
                for table_name in (
                    "durable_jobs",
                    "work_packages",
                    "run_waves",
                    "agent_runs",
                    "patch_sets",
                    "integration_steps",
                    "test_runs",
                    "quality_reports",
                    "artifacts",
                    "release_candidates",
                    "rollbacks",
                    "events",
                    "context_snapshots",
                    "code_index",
                ):
                    table = self.tables[table_name]
                    if "run_id" in table.c:
                        conn.execute(delete(table).where(table.c.run_id.in_(run_ids)))
                conn.execute(delete(runs_table).where(runs_table.c.id.in_(run_ids)))
            conn.execute(delete(projects_table).where(projects_table.c.id.in_(unique_project_ids)))
        return {
            "deleted_projects": unique_project_ids,
            "deleted_runs": len(run_ids),
            "deleted_jobs": 0,
            "deleted_artifacts": 0,
        }

    def create_run(self, tenant_id: str, project_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["runs"]
        now = utc_now()
        run_id = new_id()
        continuation = {
            "schema_version": "6.1",
            "run_id": run_id,
            "checkpoint": "run_created",
            "current_wave": None,
            "completed_waves": [],
            "completed_packages": [],
            "pending_packages": [],
            "leased_jobs": [],
            "patch_sets": [],
            "integration_status": "pending",
            "test_status": "pending",
            "quality_status": "pending",
            "release_status": "pending",
            "failure_reason": "",
            "next_action": "requirements_analysis",
        }
        row = {
            "id": run_id,
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "status": "queued",
            "checkpoint": "run_created",
            "continuation": continuation,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            conn.execute(insert(table).values(**row))
        current = self._clean(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), current.get("id"), "run_created", {"run_id": current.get("id", ""), "initial_projection": current})
        return current

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        table = self.tables["runs"]
        with self.engine.begin() as conn:
            return self._row(conn.execute(select(table).where(table.c.id == run_id)).first())

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        table = self.tables["runs"]
        fields["updated_at"] = utc_now()
        previous: dict[str, Any] | None = None
        with self.engine.begin() as conn:
            previous = self._row(conn.execute(select(table).where(table.c.id == run_id)).first())
            if previous is None:
                raise StoreError(f"run not found: {run_id}")
            conn.execute(update(table).where(table.c.id == run_id).values(**fields))
            row = conn.execute(select(table).where(table.c.id == run_id)).first()
        if not row:
            raise StoreError(f"run not found: {run_id}")
        current = self._row(row) or {}
        event = build_state_transition_event(previous, current)
        if event:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), run_id, event["event_type"], event)
        return current

    def upsert_wave(self, run_id: str, wave: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["run_waves"]
        now = utc_now()
        row = {
            "id": wave["id"],
            "run_id": run_id,
            "wave_key": wave["wave_key"],
            "sequence": wave["sequence"],
            "status": wave.get("status", "queued"),
            "payload": wave,
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            existing = conn.execute(select(table).where(table.c.id == wave["id"])).first()
            if existing:
                conn.execute(update(table).where(table.c.id == wave["id"]).values(status=row["status"], payload=row["payload"], updated_at=now))
            else:
                conn.execute(insert(table).values(**row))
        current = self._clean(row)
        identity = self._run_identity(run_id)
        self.add_event(identity["tenant_id"], identity["project_id"], run_id, "wave.projected", {"wave_id": current.get("id", ""), "wave_key": current.get("wave_key", ""), "sequence": current.get("sequence", 0), "status": current.get("status", ""), "upserted": True})
        return current

    def list_waves(self, run_id: str) -> list[dict[str, Any]]:
        table = self.tables["run_waves"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).where(table.c.run_id == run_id).order_by(table.c.sequence.asc())).all()
        return [self._row(row) or {} for row in rows]

    def upsert_work_package(self, run_id: str, wave_id: str, package: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["work_packages"]
        now = utc_now()
        row = {
            "id": package["id"],
            "run_id": run_id,
            "wave_id": wave_id,
            "wave_key": package["wave_key"],
            "package_key": package["package_key"],
            "role": package["role"],
            "domain": package["domain"],
            "status": package.get("status", "queued"),
            "payload": package,
            "result": package.get("result", {}),
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            existing = conn.execute(select(table).where(table.c.id == package["id"])).first()
            if existing:
                conn.execute(update(table).where(table.c.id == package["id"]).values(status=row["status"], payload=row["payload"], result=row["result"], updated_at=now))
            else:
                conn.execute(insert(table).values(**row))
        current = self._clean(row)
        identity = self._run_identity(run_id)
        self.add_event(identity["tenant_id"], identity["project_id"], run_id, "work_package.projected", {"work_package_id": current.get("id", ""), "wave_id": current.get("wave_id", ""), "package_key": current.get("package_key", ""), "role": current.get("role", ""), "status": current.get("status", "")})
        return current

    def get_work_package(self, package_id: str) -> dict[str, Any] | None:
        table = self.tables["work_packages"]
        with self.engine.begin() as conn:
            return self._row(conn.execute(select(table).where(table.c.id == package_id)).first())

    def list_work_packages(self, run_id: str) -> list[dict[str, Any]]:
        table = self.tables["work_packages"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).where(table.c.run_id == run_id).order_by(table.c.created_at.asc())).all()
        return [self._row(row) or {} for row in rows]

    def update_work_package(self, package_id: str, **fields: Any) -> dict[str, Any]:
        table = self.tables["work_packages"]
        fields["updated_at"] = utc_now()
        with self.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id == package_id).values(**fields))
            row = conn.execute(select(table).where(table.c.id == package_id)).first()
        if not row:
            raise StoreError(f"work package not found: {package_id}")
        current = self._row(row) or {}
        identity = self._run_identity(current.get("run_id"))
        self.add_event(identity["tenant_id"], identity["project_id"], current.get("run_id"), "work_package.updated", {"work_package_id": current.get("id", ""), "package_key": current.get("package_key", ""), "status": current.get("status", ""), "updated_fields": sorted(fields.keys())})
        return current

    def enqueue_job(self, tenant_id: str, job: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["durable_jobs"]
        now = utc_now()
        job_role = canonical_worker_role(str(job.get("role") or ""), str(job.get("subsystem") or ""), str(job.get("domain") or ""), str((job.get("payload") or {}).get("objective") or ""))
        row = {
            "id": job.get("id") or new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "run_id": job.get("run_id"),
            "job_type": job["job_type"],
            "role": job_role,
            "status": job.get("status", "queued"),
            "resume_key": job["resume_key"],
            "work_package_id": job.get("work_package_id"),
            "wave_id": job.get("wave_id"),
            "payload": job.get("payload") or {},
            "result": job.get("result") or {},
            "ai_budget": job.get("ai_budget") or {},
            "model_tier": job.get("model_tier") or "",
            "degraded": bool(job.get("degraded", False)),
            "subsystem": job.get("subsystem") or (job.get("payload") or {}).get("subsystem", ""),
            "depends_on": job.get("depends_on") or (job.get("payload") or {}).get("depends_on", []),
            "allowed_paths": job.get("allowed_paths") or (job.get("payload") or {}).get("allowed_paths", []),
            "attempts": int(job.get("attempts") or 0),
            "max_attempts": int(job.get("max_attempts") or 3),
            "worker_id": "",
            "lease_until": None,
            "heartbeat_at": None,
            "last_error": "",
            "created_at": now,
            "updated_at": now,
        }
        with self.engine.begin() as conn:
            existing = conn.execute(select(table).where(table.c.resume_key == row["resume_key"])).first()
            if existing:
                current = self._row(existing) or {}
                self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.enqueue_idempotent_hit", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "resume_key": current.get("resume_key", "")})
                return current
            conn.execute(insert(table).values(**row))
        current = self._clean(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.enqueued", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "resume_key": current.get("resume_key", ""), "work_package_id": current.get("work_package_id", ""), "wave_id": current.get("wave_id", ""), "max_attempts": current.get("max_attempts", 0)})
        return current

    def claim_job(self, tenant_id: str, role: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        if text is None:
            raise StoreError("SQLAlchemy dependencies are not installed")
        now = utc_now()
        lease_until = now + timedelta(seconds=lease_seconds)
        role = canonical_worker_role(role)
        sql = text(
            """
            WITH candidate AS (
              SELECT id
              FROM durable_jobs
              WHERE tenant_id = :tenant_id
                AND role = :role
                AND status IN ('queued', 'retry')
              ORDER BY created_at
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE durable_jobs
            SET status = 'leased',
                attempts = attempts + 1,
                worker_id = :worker_id,
                lease_until = :lease_until,
                heartbeat_at = :now,
                updated_at = :now
            WHERE id = (SELECT id FROM candidate)
            RETURNING *
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(sql, {"tenant_id": tenant_id, "role": role, "worker_id": worker_id, "lease_until": lease_until, "now": now}).first()
        if not row:
            self.normalize_roles()
            with self.engine.begin() as conn:
                row = conn.execute(sql, {"tenant_id": tenant_id, "role": role, "worker_id": worker_id, "lease_until": lease_until, "now": now}).first()
        current = self._row(row)
        if current:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.leased", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "attempts": current.get("attempts", 0), "lease_until": current.get("lease_until", "")})
        return current

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        table = self.tables["durable_jobs"]
        now = utc_now()
        with self.engine.begin() as conn:
            conn.execute(
                update(table)
                .where(and_(table.c.id == job_id, table.c.worker_id == worker_id, table.c.status.in_(("leased", "running"))))
                .values(heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds), updated_at=now)
            )
            row = conn.execute(select(table).where(table.c.id == job_id)).first()
        current = self._row(row)
        if current:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.heartbeat", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "lease_until": current.get("lease_until", "")})
        return current

    def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        table = self.tables["durable_jobs"]
        now = utc_now()
        with self.engine.begin() as conn:
            conn.execute(
                update(table)
                .where(and_(table.c.id == job_id, table.c.worker_id == worker_id, table.c.status == "leased"))
                .values(status="running", heartbeat_at=now, lease_until=now + timedelta(seconds=lease_seconds), updated_at=now)
            )
            row = conn.execute(select(table).where(table.c.id == job_id)).first()
        current = self._row(row)
        if current:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.started", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "lease_until": current.get("lease_until", "")})
        return current

    def finish_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["durable_jobs"]
        now = utc_now()
        with self.engine.begin() as conn:
            conn.execute(
                update(table)
                .where(and_(table.c.id == job_id, table.c.worker_id == worker_id))
                .values(status="completed", result=result, lease_until=None, heartbeat_at=now, updated_at=now)
            )
            row = conn.execute(select(table).where(table.c.id == job_id)).first()
        if not row:
            raise StoreError(f"job not found: {job_id}")
        current = self._row(row) or {}
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.completed", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "result_keys": sorted((result or {}).keys())})
        return current

    def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool = True) -> dict[str, Any]:
        table = self.tables["durable_jobs"]
        now = utc_now()
        with self.engine.begin() as conn:
            row = conn.execute(select(table).where(table.c.id == job_id)).first()
            if not row:
                raise StoreError(f"job not found: {job_id}")
            current = self._row(row) or {}
            status = "retry" if retryable and current["attempts"] < current["max_attempts"] else "dead_letter"
            conn.execute(
                update(table)
                .where(and_(table.c.id == job_id, table.c.worker_id == worker_id))
                .values(status=status, last_error=error[:2000], worker_id="", lease_until=None, heartbeat_at=now, updated_at=now)
            )
            updated_row = conn.execute(select(table).where(table.c.id == job_id)).first()
        current = self._row(updated_row) or {}
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.failed", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": worker_id, "status": current.get("status", ""), "retryable": bool(retryable), "error": error[:2000]})
        return current

    def requeue_expired_jobs(self, tenant_id: str) -> int:
        table = self.tables["durable_jobs"]
        now = utc_now()
        recovered: list[dict[str, Any]] = []
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).where(and_(table.c.tenant_id == tenant_id, table.c.status.in_(("leased", "running")), table.c.lease_until < now))).all()
            count = 0
            for row in rows:
                item = self._row(row) or {}
                previous_status = item.get("status", "")
                status = "retry" if item["attempts"] < item["max_attempts"] else "dead_letter"
                conn.execute(update(table).where(table.c.id == item["id"]).values(status=status, worker_id="", lease_until=None, updated_at=now))
                recovered.append({**item, "status": status, "previous_status": previous_status})
                count += 1
        for item in recovered:
            role = canonical_worker_role(str(item.get("role") or ""), str(item.get("subsystem") or ""), "", str((item.get("payload") or {}).get("objective") or ""))
            if role != item.get("role"):
                with self.engine.begin() as conn:
                    conn.execute(update(table).where(table.c.id == item["id"]).values(role=role, updated_at=utc_now()))
                item["role"] = role
            self.add_event(item.get("tenant_id", DEFAULT_TENANT), None, item.get("run_id"), "job.requeued_after_expiry" if item.get("status") == "retry" else "job.dead_lettered_after_expiry", {"job_id": item.get("id", ""), "job_type": item.get("job_type", ""), "role": item.get("role", ""), "previous_status": item.get("previous_status", ""), "attempts": item.get("attempts", 0), "max_attempts": item.get("max_attempts", 0)})
        return count

    def normalize_roles(self) -> dict[str, Any]:
        jobs_table = self.tables["durable_jobs"]
        packages_table = self.tables["work_packages"]
        job_updates = 0
        package_updates = 0
        with self.engine.begin() as conn:
            job_rows = conn.execute(select(jobs_table.c.id, jobs_table.c.role, jobs_table.c.subsystem, jobs_table.c.payload)).all()
            for row in job_rows:
                payload = self._row(row) or {}
                role = canonical_worker_role(str(payload.get("role") or ""), str(payload.get("subsystem") or ""), "", str((payload.get("payload") or {}).get("objective") or ""))
                if role and role != payload.get("role"):
                    conn.execute(update(jobs_table).where(jobs_table.c.id == payload["id"]).values(role=role, updated_at=utc_now()))
                    job_updates += 1
            package_rows = conn.execute(select(packages_table.c.id, packages_table.c.role, packages_table.c.domain, packages_table.c.payload)).all()
            for row in package_rows:
                payload = self._row(row) or {}
                package_payload = payload.get("payload") or {}
                role = canonical_worker_role(str(payload.get("role") or ""), str(payload.get("domain") or ""), str(package_payload.get("subsystem") or ""), str(package_payload.get("objective") or ""))
                if role and role != payload.get("role"):
                    updated_payload = dict(package_payload)
                    updated_payload["role"] = role
                    updated_payload["source_role"] = payload.get("role") or ""
                    conn.execute(update(packages_table).where(packages_table.c.id == payload["id"]).values(role=role, payload=updated_payload, updated_at=utc_now()))
                    package_updates += 1
        return {"ok": True, "job_updates": job_updates, "package_updates": package_updates}

    def list_jobs(self, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        table = self.tables["durable_jobs"]
        statement = select(table).order_by(table.c.created_at.asc())
        if run_id:
            statement = statement.where(table.c.run_id == run_id)
        if status:
            statement = statement.where(table.c.status == status)
        with self.engine.begin() as conn:
            rows = conn.execute(statement).all()
        return [self._row(row) or {} for row in rows]

    def list_events(self, run_id: str | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
        table = self.tables["events"]
        statement = select(table).order_by(table.c.created_at.asc())
        if run_id:
            statement = statement.where(table.c.run_id == run_id)
        if event_type:
            statement = statement.where(table.c.kind == event_type)
        with self.engine.begin() as conn:
            rows = conn.execute(statement).all()
        return [self._row(row) or {} for row in rows]

    def record_provider_success(self, provider: str) -> dict[str, Any]:
        provider = provider or "unknown-provider"
        table = self.tables["provider_health"]
        now = utc_now()
        with self.engine.begin() as conn:
            existing = self._row(conn.execute(select(table).where(table.c.provider == provider)).first())
            row = {
                "provider": provider,
                "status": "healthy",
                "success_count": int((existing or {}).get("success_count") or 0) + 1,
                "failure_count": int((existing or {}).get("failure_count") or 0),
                "failure_streak": 0,
                "last_error_kind": "",
                "last_error": str((existing or {}).get("last_error") or ""),
                "last_event_at": now.timestamp(),
                "retry_after_seconds": 0,
                "circuit_open_until": 0.0,
                "updated_at": now,
            }
            if existing:
                conn.execute(update(table).where(table.c.provider == provider).values(**{key: value for key, value in row.items() if key != "provider"}))
            else:
                conn.execute(insert(table).values(**row))
        return self._clean(row)

    def record_provider_failure(self, provider: str, error: str, classification: dict[str, Any]) -> dict[str, Any]:
        provider = provider or "unknown-provider"
        table = self.tables["provider_health"]
        now = utc_now()
        with self.engine.begin() as conn:
            existing = self._row(conn.execute(select(table).where(table.c.provider == provider)).first()) or {}
            failure_streak = int(existing.get("failure_streak") or 0) + 1
            retry_after = int(classification.get("backoff_seconds") or 0)
            retryable = bool(classification.get("retryable"))
            status = "circuit_open" if retryable and failure_streak >= 5 else "degraded" if retryable else "blocked"
            row = {
                "provider": provider,
                "status": status,
                "success_count": int(existing.get("success_count") or 0),
                "failure_count": int(existing.get("failure_count") or 0) + 1,
                "failure_streak": failure_streak,
                "last_error_kind": str(classification.get("error_kind") or "provider_unknown"),
                "last_error": str(error or "")[:1000],
                "last_event_at": now.timestamp(),
                "retry_after_seconds": retry_after,
                "circuit_open_until": now.timestamp() + retry_after if retryable and retry_after else 0.0,
                "updated_at": now,
            }
            if existing:
                conn.execute(update(table).where(table.c.provider == provider).values(**{key: value for key, value in row.items() if key != "provider"}))
            else:
                conn.execute(insert(table).values(**row))
        return self._clean(row)

    def provider_health_snapshot(self) -> dict[str, Any]:
        table = self.tables["provider_health"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).order_by(table.c.updated_at.desc())).all()
        items = [self._row(row) or {} for row in rows]
        degraded = [item for item in items if item.get("status") in {"degraded", "circuit_open", "blocked"}]
        return {
            "schema_version": "6.2",
            "ok": not any(item.get("status") == "blocked" for item in items),
            "provider_count": len(items),
            "degraded_count": len(degraded),
            "items": items,
        }

    def acquire_ai_slot(
        self,
        *,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        table = self.tables["ai_call_slots"]
        started = utc_now()
        deadline = started + timedelta(seconds=max(0, int(wait_seconds)))
        provider = provider or "unknown-provider"
        provider_limit = max(1, int(provider_limit or 1))
        run_limit = max(1, int(run_limit or 1))
        while True:
            now = utc_now()
            lease_until = now + timedelta(seconds=max(30, int(lease_seconds or 30)))
            acquired: dict[str, Any] | None = None
            with self.engine.begin() as conn:
                conn.execute(text("LOCK TABLE ai_call_slots IN SHARE ROW EXCLUSIVE MODE"))
                conn.execute(update(table).where(and_(table.c.status == "active", table.c.lease_until < now)).values(status="expired", released_at=now))
                provider_active = int(conn.execute(select(func.count()).select_from(table).where(and_(table.c.provider == provider, table.c.status == "active", table.c.lease_until >= now))).scalar_one())
                run_active = int(conn.execute(select(func.count()).select_from(table).where(and_(table.c.run_id == run_id, table.c.status == "active", table.c.lease_until >= now))).scalar_one())
                if provider_active < provider_limit and run_active < run_limit:
                    row = {
                        "id": new_id(),
                        "tenant_id": tenant_id or DEFAULT_TENANT,
                        "run_id": run_id,
                        "provider": provider or "unknown-provider",
                        "agent_run_id": agent_run_id,
                        "task_kind": task_kind,
                        "status": "active",
                        "acquired_at": now,
                        "lease_until": lease_until,
                        "released_at": None,
                    }
                    conn.execute(insert(table).values(**row))
                    acquired = {**self._clean(row), "wait_ms": round((utc_now() - started).total_seconds() * 1000)}
            if acquired:
                self.add_event(tenant_id or DEFAULT_TENANT, None, run_id, "ai_slot.acquired", {"slot_id": acquired["id"], "provider": provider, "agent_run_id": agent_run_id, "task_kind": task_kind, "provider_limit": provider_limit, "run_limit": run_limit, "wait_ms": acquired["wait_ms"]})
                return acquired
            if utc_now() >= deadline:
                self.add_event(tenant_id or DEFAULT_TENANT, None, run_id, "ai_slot.wait_timeout", {"provider": provider, "agent_run_id": agent_run_id, "task_kind": task_kind, "provider_limit": provider_limit, "run_limit": run_limit})
                raise StoreError("AI concurrency slot wait timed out")
            import time as _time

            _time.sleep(0.2)

    def release_ai_slot(self, slot_id: str) -> dict[str, Any]:
        table = self.tables["ai_call_slots"]
        now = utc_now()
        with self.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id == slot_id).values(status="released", released_at=now))
            row = conn.execute(select(table).where(table.c.id == slot_id)).first()
        current = self._row(row) or {}
        if current:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "ai_slot.released", {"slot_id": current.get("id", ""), "provider": current.get("provider", ""), "agent_run_id": current.get("agent_run_id", ""), "task_kind": current.get("task_kind", "")})
        return current

    def ai_slot_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        table = self.tables["ai_call_slots"]
        statement = select(table).order_by(table.c.acquired_at.desc())
        if run_id:
            statement = statement.where(table.c.run_id == run_id)
        with self.engine.begin() as conn:
            rows = conn.execute(statement).all()
        items = [self._row(row) or {} for row in rows]
        active = [item for item in items if item.get("status") == "active"]
        return {"schema_version": "6.2", "active_count": len(active), "slot_count": len(items), "items": items[:200]}

    def purge_retired_generation_records(self) -> dict[str, Any]:
        patterns = ("v" + "5", "V" + "5")
        projects = [
            project
            for project in self.list_projects(DEFAULT_TENANT)
            if any(pattern in stable_json(project) for pattern in patterns)
        ]
        project_ids = [project["id"] for project in projects]
        result = self.delete_projects(project_ids) if project_ids else {"deleted_projects": [], "deleted_runs": 0}
        return {"ok": True, "deleted_project_count": len(project_ids), **result}

    def add_artifact(self, tenant_id: str, project_id: str | None, artifact: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["artifacts"]
        now = utc_now()
        row = {
            "id": artifact.get("id") or new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "run_id": artifact.get("run_id"),
            "kind": artifact["kind"],
            "path": artifact["path"],
            "sha256": artifact.get("sha256", ""),
            "size": int(artifact.get("size") or 0),
            "payload": artifact.get("payload") or {},
            "metadata": artifact.get("metadata") or {},
            "created_at": now,
        }
        with self.engine.begin() as conn:
            conn.execute(insert(table).values(**row))
        current = self._clean(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), current.get("run_id"), "artifact.recorded", {"artifact_id": current.get("id", ""), "kind": current.get("kind", ""), "path": current.get("path", ""), "sha256": current.get("sha256", ""), "size": current.get("size", 0)})
        return current

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        table = self.tables["artifacts"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(table).where(table.c.run_id == run_id).order_by(table.c.created_at.asc())).all()
        return [self._row(row) or {} for row in rows]

    def add_event(self, tenant_id: str, project_id: str | None, run_id: str | None, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        table = self.tables["events"]
        counter_table = self.tables["event_counters"]
        now = utc_now()
        with self.engine.begin() as conn:
            aggregate_type = "run" if run_id else ("project" if project_id else "tenant")
            aggregate_id = run_id or project_id or tenant_id
            sequence_stmt = pg_insert(counter_table).values(aggregate_type=aggregate_type, aggregate_id=aggregate_id, sequence=1, updated_at=now)
            sequence_stmt = sequence_stmt.on_conflict_do_update(
                index_elements=[counter_table.c.aggregate_type, counter_table.c.aggregate_id],
                set_={"sequence": counter_table.c.sequence + 1, "updated_at": now},
            ).returning(counter_table.c.sequence)
            sequence = int(conn.execute(sequence_stmt).scalar_one())
            row = self._event_envelope(tenant_id=tenant_id, project_id=project_id, run_id=run_id, event_type=event_type, payload=payload, metadata={"sequence": sequence}, conn=conn)
            row["created_at"] = now
            conn.execute(insert(table).values(**row))
        return self._clean(row)


class InMemoryV6Store(V6Store):
    def __init__(self):
        self.tenants: dict[str, dict[str, Any]] = {}
        self.projects: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.waves: dict[str, dict[str, Any]] = {}
        self.packages: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.artifacts: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.provider_states: dict[str, dict[str, Any]] = {}
        self.ai_slots: dict[str, dict[str, Any]] = {}

    def bootstrap(self) -> None:
        self.get_or_create_tenant(DEFAULT_TENANT)
        self.normalize_roles()

    def _copy(self, value: Any) -> Any:
        return copy.deepcopy(value)

    def _run_identity(self, run_id: str | None) -> dict[str, Any]:
        run = self.runs.get(run_id or "")
        if not run:
            return {"tenant_id": DEFAULT_TENANT, "project_id": None}
        return {"tenant_id": run.get("tenant_id") or DEFAULT_TENANT, "project_id": run.get("project_id")}

    def _event_envelope(self, tenant_id: str, project_id: str | None, run_id: str | None, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = [event for event in self.events.values() if event.get("run_id") == run_id] if run_id else [event for event in self.events.values() if event.get("project_id") == project_id] if project_id else [event for event in self.events.values() if event.get("tenant_id") == tenant_id]
        payload_hash = sha256_bytes(stable_json(payload).encode("utf-8"))
        return {
            "id": new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "run_id": run_id,
            "kind": event_type,
            "path": "",
            "sha256": payload_hash,
            "size": len(stable_json(payload).encode("utf-8")),
            "payload": payload,
            "metadata": {
                "event_type": event_type,
                "schema_version": "6.1",
                "aggregate_type": "run" if run_id else ("project" if project_id else "tenant"),
                "aggregate_id": run_id or project_id or tenant_id,
                "payload_hash": payload_hash,
                "sequence": len(existing) + 1,
                "idempotency_key": str(payload.get("idempotency_key") or payload.get("resume_key") or payload.get("job_id") or ""),
            },
            "created_at": utc_now().isoformat(),
        }

    def get_or_create_tenant(self, tenant_id: str = DEFAULT_TENANT) -> dict[str, Any]:
        tenant_id = tenant_id or DEFAULT_TENANT
        if tenant_id not in self.tenants:
            self.tenants[tenant_id] = {"id": tenant_id, "name": tenant_id, "created_at": utc_now().isoformat()}
        return self._copy(self.tenants[tenant_id])

    def create_project(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        row = {
            "id": new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "name": payload["name"],
            "title": payload.get("title") or payload["name"],
            "description": payload.get("description") or "",
            "project_path": payload.get("project_path") or "",
            "config": payload.get("config") or {},
            "status": "created",
            "created_at": now,
            "updated_at": now,
        }
        self.projects[row["id"]] = row
        current = self._copy(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("id"), None, "project.created", {"project_id": current.get("id", ""), "name": current.get("name", "")})
        return current

    def list_projects(self, tenant_id: str) -> list[dict[str, Any]]:
        return [self._copy(project) for project in sorted(self.projects.values(), key=lambda item: item["created_at"], reverse=True) if project["tenant_id"] == tenant_id]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        project = self.projects.get(project_id)
        return self._copy(project) if project else None

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        return [self._copy(run) for run in sorted(self.runs.values(), key=lambda item: item["created_at"], reverse=True) if run["project_id"] == project_id]

    def delete_project(self, project_id: str) -> dict[str, Any]:
        return self.delete_projects([project_id])

    def delete_projects(self, project_ids: list[str]) -> dict[str, Any]:
        unique_project_ids = [project_id for project_id in dict.fromkeys(project_ids) if project_id]
        deleted_runs = 0
        if not unique_project_ids:
            return {"deleted_projects": [], "deleted_runs": 0, "deleted_jobs": 0, "deleted_artifacts": 0}
        run_ids = [run_id for run_id, run in self.runs.items() if run["project_id"] in unique_project_ids]
        deleted_runs = len(run_ids)
        for run_id in run_ids:
            self.waves = {key: value for key, value in self.waves.items() if value["run_id"] != run_id}
            self.packages = {key: value for key, value in self.packages.items() if value["run_id"] != run_id}
            self.jobs = {key: value for key, value in self.jobs.items() if value["run_id"] != run_id}
            self.artifacts = {key: value for key, value in self.artifacts.items() if value["run_id"] != run_id}
            self.events = {key: value for key, value in self.events.items() if value["run_id"] != run_id}
            del self.runs[run_id]
        for project_id in unique_project_ids:
            self.projects.pop(project_id, None)
        return {
            "deleted_projects": unique_project_ids,
            "deleted_runs": deleted_runs,
            "deleted_jobs": 0,
            "deleted_artifacts": 0,
        }

    def create_run(self, tenant_id: str, project_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        run_id = new_id()
        continuation = {
            "schema_version": "6.1",
            "run_id": run_id,
            "checkpoint": "run_created",
            "current_wave": None,
            "completed_waves": [],
            "completed_packages": [],
            "pending_packages": [],
            "leased_jobs": [],
            "patch_sets": [],
            "integration_status": "pending",
            "test_status": "pending",
            "quality_status": "pending",
            "release_status": "pending",
            "failure_reason": "",
            "next_action": "requirements_analysis",
        }
        row = {
            "id": run_id,
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "status": "queued",
            "checkpoint": "run_created",
            "continuation": continuation,
            "metadata": metadata,
            "created_at": now,
            "updated_at": now,
        }
        self.runs[run_id] = row
        current = self._copy(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), current.get("id"), "run_created", {"run_id": current.get("id", ""), "initial_projection": current})
        return current

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        return self._copy(run) if run else None

    def update_run(self, run_id: str, **fields: Any) -> dict[str, Any]:
        if run_id not in self.runs:
            raise StoreError(f"run not found: {run_id}")
        previous = self._copy(self.runs[run_id])
        self.runs[run_id].update(fields)
        self.runs[run_id]["updated_at"] = utc_now().isoformat()
        current = self._copy(self.runs[run_id])
        event = build_state_transition_event(previous, current)
        if event:
            self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), run_id, event["event_type"], event)
        return current

    def upsert_wave(self, run_id: str, wave: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        row = {
            "id": wave["id"],
            "run_id": run_id,
            "wave_key": wave["wave_key"],
            "sequence": wave["sequence"],
            "status": wave.get("status", "queued"),
            "payload": wave,
            "created_at": self.waves.get(wave["id"], {}).get("created_at", now),
            "updated_at": now,
        }
        self.waves[row["id"]] = row
        current = self._copy(row)
        identity = self._run_identity(run_id)
        self.add_event(identity["tenant_id"], identity["project_id"], run_id, "wave.projected", {"wave_id": current.get("id", ""), "wave_key": current.get("wave_key", ""), "sequence": current.get("sequence", 0), "status": current.get("status", ""), "upserted": True})
        return current

    def list_waves(self, run_id: str) -> list[dict[str, Any]]:
        return [self._copy(wave) for wave in sorted(self.waves.values(), key=lambda item: item["sequence"]) if wave["run_id"] == run_id]

    def upsert_work_package(self, run_id: str, wave_id: str, package: dict[str, Any]) -> dict[str, Any]:
        now = utc_now().isoformat()
        row = {
            "id": package["id"],
            "run_id": run_id,
            "wave_id": wave_id,
            "wave_key": package["wave_key"],
            "package_key": package["package_key"],
            "role": package["role"],
            "domain": package["domain"],
            "status": package.get("status", "queued"),
            "payload": package,
            "result": package.get("result", {}),
            "created_at": self.packages.get(package["id"], {}).get("created_at", now),
            "updated_at": now,
        }
        self.packages[row["id"]] = row
        current = self._copy(row)
        identity = self._run_identity(run_id)
        self.add_event(identity["tenant_id"], identity["project_id"], run_id, "work_package.projected", {"work_package_id": current.get("id", ""), "wave_id": current.get("wave_id", ""), "package_key": current.get("package_key", ""), "role": current.get("role", ""), "status": current.get("status", "")})
        return current

    def get_work_package(self, package_id: str) -> dict[str, Any] | None:
        package = self.packages.get(package_id)
        return self._copy(package) if package else None

    def list_work_packages(self, run_id: str) -> list[dict[str, Any]]:
        return [self._copy(package) for package in self.packages.values() if package["run_id"] == run_id]

    def update_work_package(self, package_id: str, **fields: Any) -> dict[str, Any]:
        if package_id not in self.packages:
            raise StoreError(f"work package not found: {package_id}")
        self.packages[package_id].update(fields)
        self.packages[package_id]["updated_at"] = utc_now().isoformat()
        current = self._copy(self.packages[package_id])
        identity = self._run_identity(current.get("run_id"))
        self.add_event(identity["tenant_id"], identity["project_id"], current.get("run_id"), "work_package.updated", {"work_package_id": current.get("id", ""), "package_key": current.get("package_key", ""), "status": current.get("status", ""), "updated_fields": sorted(fields.keys())})
        return current

    def enqueue_job(self, tenant_id: str, job: dict[str, Any]) -> dict[str, Any]:
        for existing in self.jobs.values():
            if existing["resume_key"] == job["resume_key"]:
                current = self._copy(existing)
                self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.enqueue_idempotent_hit", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "resume_key": current.get("resume_key", "")})
                return current
        now = utc_now().isoformat()
        job_role = canonical_worker_role(str(job.get("role") or ""), str(job.get("subsystem") or ""), str(job.get("domain") or ""), str((job.get("payload") or {}).get("objective") or ""))
        row = {
            "id": job.get("id") or new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "run_id": job.get("run_id"),
            "job_type": job["job_type"],
            "role": job_role,
            "status": job.get("status", "queued"),
            "resume_key": job["resume_key"],
            "work_package_id": job.get("work_package_id"),
            "wave_id": job.get("wave_id"),
            "payload": job.get("payload") or {},
            "result": job.get("result") or {},
            "ai_budget": job.get("ai_budget") or {},
            "model_tier": job.get("model_tier") or "",
            "degraded": bool(job.get("degraded", False)),
            "subsystem": job.get("subsystem") or (job.get("payload") or {}).get("subsystem", ""),
            "depends_on": job.get("depends_on") or (job.get("payload") or {}).get("depends_on", []),
            "allowed_paths": job.get("allowed_paths") or (job.get("payload") or {}).get("allowed_paths", []),
            "attempts": int(job.get("attempts") or 0),
            "max_attempts": int(job.get("max_attempts") or 3),
            "worker_id": "",
            "lease_until": None,
            "heartbeat_at": None,
            "last_error": "",
            "created_at": now,
            "updated_at": now,
        }
        self.jobs[row["id"]] = row
        current = self._copy(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.enqueued", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "resume_key": current.get("resume_key", ""), "work_package_id": current.get("work_package_id", ""), "wave_id": current.get("wave_id", ""), "max_attempts": current.get("max_attempts", 0)})
        return current

    def claim_job(self, tenant_id: str, role: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        role = canonical_worker_role(role)
        for job in sorted(self.jobs.values(), key=lambda item: item["created_at"]):
            if job["tenant_id"] == tenant_id and job["role"] == role and job["status"] in {"queued", "retry"}:
                job["status"] = "leased"
                job["attempts"] += 1
                job["worker_id"] = worker_id
                lease_until = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                job["lease_until"] = lease_until.isoformat()
                job["heartbeat_at"] = utc_now().isoformat()
                job["updated_at"] = utc_now().isoformat()
                current = self._copy(job)
                self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.leased", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "attempts": current.get("attempts", 0), "lease_until": current.get("lease_until", "")})
                return current
        self.normalize_roles()
        for job in sorted(self.jobs.values(), key=lambda item: item["created_at"]):
            if job["tenant_id"] == tenant_id and job["role"] == role and job["status"] in {"queued", "retry"}:
                job["status"] = "leased"
                job["attempts"] += 1
                job["worker_id"] = worker_id
                lease_until = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                job["lease_until"] = lease_until.isoformat()
                job["heartbeat_at"] = utc_now().isoformat()
                job["updated_at"] = utc_now().isoformat()
                current = self._copy(job)
                self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.leased", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "attempts": current.get("attempts", 0), "lease_until": current.get("lease_until", "")})
                return current
        return None

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if not job or job["worker_id"] != worker_id:
            return None
        job["heartbeat_at"] = utc_now().isoformat()
        job["lease_until"] = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        job["updated_at"] = utc_now().isoformat()
        current = self._copy(job)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.heartbeat", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "lease_until": current.get("lease_until", "")})
        return current

    def start_job(self, job_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        if not job or job["worker_id"] != worker_id or job["status"] != "leased":
            return None
        job["status"] = "running"
        job["heartbeat_at"] = utc_now().isoformat()
        job["lease_until"] = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()
        job["updated_at"] = utc_now().isoformat()
        current = self._copy(job)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.started", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "lease_until": current.get("lease_until", "")})
        return current

    def finish_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs[job_id]
        if job["worker_id"] != worker_id:
            raise StoreError("worker does not own job")
        job["status"] = "completed"
        job["result"] = result
        job["lease_until"] = None
        job["updated_at"] = utc_now().isoformat()
        current = self._copy(job)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.completed", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": current.get("worker_id", ""), "result_keys": sorted((result or {}).keys())})
        return current

    def fail_job(self, job_id: str, worker_id: str, error: str, retryable: bool = True) -> dict[str, Any]:
        job = self.jobs[job_id]
        if job["worker_id"] != worker_id:
            raise StoreError("worker does not own job")
        job["status"] = "retry" if retryable and job["attempts"] < job["max_attempts"] else "dead_letter"
        job["worker_id"] = ""
        job["lease_until"] = None
        job["last_error"] = error[:2000]
        job["updated_at"] = utc_now().isoformat()
        current = self._copy(job)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "job.failed", {"job_id": current.get("id", ""), "job_type": current.get("job_type", ""), "role": current.get("role", ""), "worker_id": worker_id, "status": current.get("status", ""), "retryable": bool(retryable), "error": error[:2000]})
        return current

    def requeue_expired_jobs(self, tenant_id: str) -> int:
        now = datetime.now(timezone.utc)
        count = 0
        recovered: list[dict[str, Any]] = []
        for job in self.jobs.values():
            lease_until = job.get("lease_until")
            if not lease_until:
                continue
            if job["tenant_id"] == tenant_id and job["status"] in {"leased", "running"} and datetime.fromisoformat(lease_until) < now:
                previous_status = job["status"]
                job["status"] = "retry" if job["attempts"] < job["max_attempts"] else "dead_letter"
                job["worker_id"] = ""
                job["lease_until"] = None
                job["updated_at"] = utc_now().isoformat()
                recovered.append({**self._copy(job), "previous_status": previous_status})
                count += 1
        for item in recovered:
            item["role"] = canonical_worker_role(str(item.get("role") or ""), str(item.get("subsystem") or ""), "", str((item.get("payload") or {}).get("objective") or ""))
            self.add_event(item.get("tenant_id", DEFAULT_TENANT), None, item.get("run_id"), "job.requeued_after_expiry" if item.get("status") == "retry" else "job.dead_lettered_after_expiry", {"job_id": item.get("id", ""), "job_type": item.get("job_type", ""), "role": item.get("role", ""), "previous_status": item.get("previous_status", ""), "attempts": item.get("attempts", 0), "max_attempts": item.get("max_attempts", 0)})
        return count

    def normalize_roles(self) -> dict[str, Any]:
        job_updates = 0
        package_updates = 0
        for job in self.jobs.values():
            role = canonical_worker_role(str(job.get("role") or ""), str(job.get("subsystem") or ""), "", str((job.get("payload") or {}).get("objective") or ""))
            if role and role != job.get("role"):
                job["role"] = role
                job["updated_at"] = utc_now().isoformat()
                job_updates += 1
        for package in self.packages.values():
            package_payload = package.get("payload") or {}
            original_role = package.get("role") or ""
            role = canonical_worker_role(str(package.get("role") or ""), str(package.get("domain") or ""), str(package_payload.get("subsystem") or ""), str(package_payload.get("objective") or ""))
            if role and role != package.get("role"):
                package["role"] = role
                package_payload = dict(package_payload)
                package_payload["role"] = role
                package_payload["source_role"] = package_payload.get("source_role") or original_role
                package["payload"] = package_payload
                package["updated_at"] = utc_now().isoformat()
                package_updates += 1
        return {"ok": True, "job_updates": job_updates, "package_updates": package_updates}

    def list_jobs(self, run_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list(self.jobs.values())
        if run_id:
            jobs = [job for job in jobs if job["run_id"] == run_id]
        if status:
            jobs = [job for job in jobs if job["status"] == status]
        return [self._copy(job) for job in sorted(jobs, key=lambda item: item["created_at"])]

    def list_events(self, run_id: str | None = None, event_type: str | None = None) -> list[dict[str, Any]]:
        events = list(self.events.values())
        if run_id:
            events = [event for event in events if event["run_id"] == run_id]
        if event_type:
            events = [event for event in events if event["kind"] == event_type]
        return [self._copy(event) for event in sorted(events, key=lambda item: item["created_at"])]

    def add_artifact(self, tenant_id: str, project_id: str | None, artifact: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": artifact.get("id") or new_id(),
            "tenant_id": tenant_id or DEFAULT_TENANT,
            "project_id": project_id,
            "run_id": artifact.get("run_id"),
            "kind": artifact["kind"],
            "path": artifact["path"],
            "sha256": artifact.get("sha256", ""),
            "size": int(artifact.get("size") or 0),
            "payload": artifact.get("payload") or {},
            "metadata": artifact.get("metadata") or {},
            "created_at": utc_now().isoformat(),
        }
        self.artifacts[row["id"]] = row
        current = self._copy(row)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), current.get("project_id"), current.get("run_id"), "artifact.recorded", {"artifact_id": current.get("id", ""), "kind": current.get("kind", ""), "path": current.get("path", ""), "sha256": current.get("sha256", ""), "size": current.get("size", 0)})
        return current

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return [self._copy(artifact) for artifact in self.artifacts.values() if artifact["run_id"] == run_id]

    def add_event(self, tenant_id: str, project_id: str | None, run_id: str | None, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._event_envelope(tenant_id, project_id, run_id, event_type, payload)
        self.events[row["id"]] = row
        return self._copy(row)

    def record_provider_success(self, provider: str) -> dict[str, Any]:
        provider = provider or "unknown-provider"
        now = time_time()
        existing = self.provider_states.get(provider, {})
        row = {
            "provider": provider,
            "status": "healthy",
            "success_count": int(existing.get("success_count") or 0) + 1,
            "failure_count": int(existing.get("failure_count") or 0),
            "failure_streak": 0,
            "last_error_kind": "",
            "last_error": str(existing.get("last_error") or ""),
            "last_event_at": now,
            "retry_after_seconds": 0,
            "circuit_open_until": 0.0,
            "updated_at": utc_now().isoformat(),
        }
        self.provider_states[provider] = row
        return self._copy(row)

    def record_provider_failure(self, provider: str, error: str, classification: dict[str, Any]) -> dict[str, Any]:
        provider = provider or "unknown-provider"
        now = time_time()
        existing = self.provider_states.get(provider, {})
        failure_streak = int(existing.get("failure_streak") or 0) + 1
        retry_after = int(classification.get("backoff_seconds") or 0)
        retryable = bool(classification.get("retryable"))
        status = "circuit_open" if retryable and failure_streak >= 5 else "degraded" if retryable else "blocked"
        row = {
            "provider": provider,
            "status": status,
            "success_count": int(existing.get("success_count") or 0),
            "failure_count": int(existing.get("failure_count") or 0) + 1,
            "failure_streak": failure_streak,
            "last_error_kind": str(classification.get("error_kind") or "provider_unknown"),
            "last_error": str(error or "")[:1000],
            "last_event_at": now,
            "retry_after_seconds": retry_after,
            "circuit_open_until": now + retry_after if retryable and retry_after else 0.0,
            "updated_at": utc_now().isoformat(),
        }
        self.provider_states[provider] = row
        return self._copy(row)

    def provider_health_snapshot(self) -> dict[str, Any]:
        items = [self._copy(item) for item in self.provider_states.values()]
        degraded = [item for item in items if item.get("status") in {"degraded", "circuit_open", "blocked"}]
        return {
            "schema_version": "6.2",
            "ok": not any(item.get("status") == "blocked" for item in items),
            "provider_count": len(items),
            "degraded_count": len(degraded),
            "items": items,
        }

    def acquire_ai_slot(
        self,
        *,
        tenant_id: str,
        run_id: str,
        provider: str,
        agent_run_id: str,
        task_kind: str,
        provider_limit: int,
        run_limit: int,
        wait_seconds: int,
        lease_seconds: int,
    ) -> dict[str, Any]:
        provider = provider or "unknown-provider"
        provider_limit = max(1, int(provider_limit or 1))
        run_limit = max(1, int(run_limit or 1))
        started = time_time()
        deadline = started + max(0, int(wait_seconds or 0))
        while True:
            now = time_time()
            for slot in self.ai_slots.values():
                if slot.get("status") == "active" and float(slot.get("lease_until_epoch") or 0) < now:
                    slot["status"] = "expired"
                    slot["released_at"] = utc_now().isoformat()
            provider_active = [slot for slot in self.ai_slots.values() if slot.get("provider") == provider and slot.get("status") == "active"]
            run_active = [slot for slot in self.ai_slots.values() if slot.get("run_id") == run_id and slot.get("status") == "active"]
            if len(provider_active) < provider_limit and len(run_active) < run_limit:
                slot_id = new_id()
                lease_until = datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds or 30)))
                row = {
                    "id": slot_id,
                    "tenant_id": tenant_id or DEFAULT_TENANT,
                    "run_id": run_id,
                    "provider": provider,
                    "agent_run_id": agent_run_id,
                    "task_kind": task_kind,
                    "status": "active",
                    "acquired_at": utc_now().isoformat(),
                    "lease_until": lease_until.isoformat(),
                    "lease_until_epoch": now + max(30, int(lease_seconds or 30)),
                    "released_at": None,
                    "wait_ms": round((time_time() - started) * 1000),
                }
                self.ai_slots[slot_id] = row
                self.add_event(tenant_id or DEFAULT_TENANT, None, run_id, "ai_slot.acquired", {"slot_id": slot_id, "provider": provider, "agent_run_id": agent_run_id, "task_kind": task_kind, "provider_limit": provider_limit, "run_limit": run_limit, "wait_ms": row["wait_ms"]})
                return self._copy(row)
            if time_time() >= deadline:
                self.add_event(tenant_id or DEFAULT_TENANT, None, run_id, "ai_slot.wait_timeout", {"provider": provider, "agent_run_id": agent_run_id, "task_kind": task_kind, "provider_limit": provider_limit, "run_limit": run_limit})
                raise StoreError("AI concurrency slot wait timed out")
            import time as _time

            _time.sleep(0.02)

    def release_ai_slot(self, slot_id: str) -> dict[str, Any]:
        slot = self.ai_slots.get(slot_id)
        if not slot:
            return {}
        slot["status"] = "released"
        slot["released_at"] = utc_now().isoformat()
        current = self._copy(slot)
        self.add_event(current.get("tenant_id", DEFAULT_TENANT), None, current.get("run_id"), "ai_slot.released", {"slot_id": current.get("id", ""), "provider": current.get("provider", ""), "agent_run_id": current.get("agent_run_id", ""), "task_kind": current.get("task_kind", "")})
        return current

    def ai_slot_snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        items = list(self.ai_slots.values())
        if run_id:
            items = [item for item in items if item.get("run_id") == run_id]
        active = [item for item in items if item.get("status") == "active"]
        return {"schema_version": "6.2", "active_count": len(active), "slot_count": len(items), "items": [self._copy(item) for item in items[:200]]}

    def purge_retired_generation_records(self) -> dict[str, Any]:
        patterns = ("v" + "5", "V" + "5")
        project_ids = [
            project_id
            for project_id, project in self.projects.items()
            if any(pattern in stable_json(project) for pattern in patterns)
        ]
        result = self.delete_projects(project_ids) if project_ids else {"deleted_projects": [], "deleted_runs": 0}
        return {"ok": True, "deleted_project_count": len(project_ids), **result}


def build_store(database_url: str) -> V6Store:
    return PostgresV6Store(database_url)

